from conftest import create_document, main
from app.models import ParseJob


class FakeAsyncResult:
    def __init__(self, job_id, app=None, *, state="PENDING", result=None):
        self.id = job_id
        self.state = state
        self.result = result


class FakeTask:
    def __init__(self):
        self.calls = []

    def apply_async(self, *, args, task_id):
        self.calls.append({"args": args, "task_id": task_id})


class FakeRedis:
    def __init__(self, *args, **kwargs):
        self.locks = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.locks:
            return False
        self.locks[key] = value
        return True


def test_parse_enqueues_job_and_reuses_active_job(client, db, auth_headers, monkeypatch):
    doc = create_document(db, filename="pytest-parse.pdf")
    task = FakeTask()

    monkeypatch.setattr(main, "parse_document_task", task)
    monkeypatch.setattr(
        main,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(job_id, state="STARTED"),
    )
    monkeypatch.setattr(main, "celery_has_known_job", lambda job_id: True)

    first = client.post(f"/documents/{doc.id}/parse", headers=auth_headers)

    assert first.status_code == 202
    first_body = first.json()
    assert first_body["status"] == "queued"
    assert len(task.calls) == 1
    assert task.calls[0]["args"] == [doc.id]
    assert task.calls[0]["task_id"] == first_body["job_id"]

    db.refresh(doc)
    assert doc.status == "queued"
    assert doc.current_job_id == first_body["job_id"]
    job = db.query(ParseJob).filter_by(id=first_body["job_id"]).first()
    assert job.status == "queued"
    assert job.document_id == doc.id

    second = client.post(f"/documents/{doc.id}/parse", headers=auth_headers)

    assert second.status_code == 200
    assert second.json()["job_id"] == first_body["job_id"]
    assert second.json()["status"] == "started"
    assert len(task.calls) == 1


def test_parse_replaces_stale_pending_job(client, db, auth_headers, monkeypatch):
    doc = create_document(
        db,
        filename="pytest-stale-pending.pdf",
        status="queued",
        current_job_id="old-job-id",
    )
    task = FakeTask()

    monkeypatch.setattr(main, "parse_document_task", task)
    monkeypatch.setattr(
        main,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(job_id, state="PENDING"),
    )
    monkeypatch.setattr(main, "broker_has_pending_job", lambda job_id: False)

    response = client.post(f"/documents/{doc.id}/parse", headers=auth_headers)

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["job_id"] != "old-job-id"
    assert task.calls == [{"args": [doc.id], "task_id": response.json()["job_id"]}]


def test_job_poll_recovers_orphaned_started_job(client, db, auth_headers, monkeypatch):
    doc = create_document(
        db,
        filename="pytest-orphaned.pdf",
        status="parsing",
        current_job_id="orphan-job-id",
    )
    task = FakeTask()

    monkeypatch.setattr(main, "parse_document_task", task)
    monkeypatch.setattr(main, "celery_has_known_job", lambda job_id: False)
    monkeypatch.setattr(main.redis, "Redis", FakeRedis)
    monkeypatch.setattr(
        main,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(job_id, state="STARTED"),
    )

    response = client.get("/jobs/orphan-job-id", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "orphan-job-id",
        "status": "queued",
        "recovered": True,
    }
    assert task.calls == [{"args": [doc.id], "task_id": "orphan-job-id"}]

    db.refresh(doc)
    assert doc.status == "queued"
    assert doc.current_job_id == "orphan-job-id"


def test_job_success_serializes_result(client, auth_headers, monkeypatch):
    result = {"id": 99, "status": "verified"}
    monkeypatch.setattr(
        main,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(job_id, state="SUCCESS", result=result),
    )

    response = client.get("/jobs/success-job-id", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "success-job-id",
        "status": "success",
        "result": result,
    }

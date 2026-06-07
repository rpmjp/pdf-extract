from conftest import create_document
from app.priority import compute_priority


def test_compute_priority_branches():
    assert compute_priority("failed", None) == "P1"
    assert compute_priority("needs_review", 0.59) == "P1"
    assert compute_priority("needs_review", 0.6) == "P2"
    assert compute_priority("needs_review", None) == "P2"
    assert compute_priority("verified", 0.4) == "P3"
    assert compute_priority("approved", 0.4) == "P3"
    assert compute_priority("uploaded", None) is None
    assert compute_priority("queued", None) is None
    assert compute_priority("parsing", None) is None


def test_documents_return_priority_and_sort_p1_first(client, db, auth_headers):
    p3 = create_document(db, filename="pytest-priority-p3.pdf", status="verified")
    p1 = create_document(db, filename="pytest-priority-p1.pdf", status="failed")
    p2 = create_document(db, filename="pytest-priority-p2.pdf", status="needs_review")
    none = create_document(db, filename="pytest-priority-none.pdf", status="queued")
    p1.confidence_score = 0.9
    p2.confidence_score = 0.7
    p3.confidence_score = 0.9
    db.commit()

    response = client.get("/documents?q=pytest-priority&per_page=100", headers=auth_headers)

    assert response.status_code == 200
    rows = [row for row in response.json()["items"] if row["id"] in {p1.id, p2.id, p3.id, none.id}]
    assert [row["priority"] for row in rows] == ["P1", "P2", "P3", None]


def test_documents_priority_filter(client, db, auth_headers):
    p1 = create_document(db, filename="pytest-priority-filter-p1.pdf", status="needs_review")
    p2 = create_document(db, filename="pytest-priority-filter-p2.pdf", status="needs_review")
    p3 = create_document(db, filename="pytest-priority-filter-p3.pdf", status="approved")
    p1.confidence_score = 0.4
    p2.confidence_score = 0.7
    p3.confidence_score = 0.9
    db.commit()

    response = client.get("/documents?priority=P1", headers=auth_headers)

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["items"]}
    assert p1.id in ids
    assert p2.id not in ids
    assert p3.id not in ids

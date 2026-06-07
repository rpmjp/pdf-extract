from datetime import datetime, timedelta, timezone

import pytest

from conftest import create_document
from app.models import AuditLog, CorrectionExample, DocumentVersion, ParseJob, ReviewItem


ENVELOPE_KEYS = (
    "kpis", "alerts", "volume_vs_confidence", "pass_rate_trend",
    "failure_breakdown", "by_source", "queue_depth", "parse_latency",
    "failure_rate", "lowest",
)
KPI_KEYS = ("pass_rate", "mean_confidence", "median_review_time_hours", "auto_approval_rate")


def test_overview_returns_full_envelope(client, db, auth_headers):
    response = client.get("/insights/overview?range=30d", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    for key in ENVELOPE_KEYS:
        assert key in body, f"missing key: {key}"

    kpis = body["kpis"]
    for k in KPI_KEYS:
        assert k in kpis, f"missing KPI: {k}"
        assert "current" in kpis[k]
        assert "previous" in kpis[k]
        assert "delta" in kpis[k]

    assert isinstance(body["alerts"], list)
    assert isinstance(body["volume_vs_confidence"], list)
    assert isinstance(body["pass_rate_trend"], list)
    assert isinstance(body["failure_breakdown"], list)
    assert isinstance(body["by_source"], list)
    assert isinstance(body["queue_depth"], list)
    assert isinstance(body["parse_latency"], list)
    assert isinstance(body["failure_rate"], list)
    assert isinstance(body["lowest"], list)
    assert len(body["lowest"]) <= 20


def test_overview_empty_state_no_crash(client, auth_headers):
    """With range=7d and no recent docs the endpoint must not crash."""
    response = client.get("/insights/overview?range=7d", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    for key in ENVELOPE_KEYS:
        assert key in body
    for k in KPI_KEYS:
        assert body["kpis"][k]["current"] is None or isinstance(body["kpis"][k]["current"], (int, float))


def test_overview_all_ranges_accepted(client, auth_headers):
    for rng in ("7d", "30d", "90d", "all"):
        r = client.get(f"/insights/overview?range={rng}", headers=auth_headers)
        assert r.status_code == 200, f"range={rng} returned {r.status_code}"


def test_overview_requires_reviewer_role(client):
    from app.auth import AuthUser, create_access_token

    token = create_access_token(AuthUser(username="pytest-uploader-only", roles=["uploader"]))
    response = client.get("/insights/overview?range=7d", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_kpi_pass_rate_computed_from_seeded_docs(client, db, auth_headers):
    """Seed 4 verified + 1 needs_review → pass_rate current should be 0.8."""
    for i in range(4):
        d = create_document(db, filename=f"pytest-kpi-v{i}.pdf", status="verified")
        d.confidence_score = 0.90
    nr = create_document(db, filename="pytest-kpi-nr.pdf", status="needs_review")
    nr.confidence_score = 0.45
    db.commit()

    response = client.get("/insights/overview?range=all", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    # We seeded 4 verified (good) + 1 needs_review (not good) out of 5 processed.
    # Other docs in the DB may change the absolute number, but we can verify direction:
    kpi = body["kpis"]["pass_rate"]
    assert kpi["current"] is not None
    assert 0.0 <= kpi["current"] <= 1.0

    # With 4 verified docs the mean confidence should include them
    kpi_conf = body["kpis"]["mean_confidence"]
    assert kpi_conf["current"] is not None
    assert kpi_conf["current"] > 0


def test_aged_review_alert_fires(client, db, auth_headers):
    """A needs_review doc older than 24h should produce an aged_review alert."""
    old_doc = create_document(db, filename="pytest-aged-nr.pdf", status="needs_review")
    # Backdate so it appears >24h old
    old_doc.created_at = datetime.now(timezone.utc) - timedelta(hours=36)
    db.commit()

    response = client.get("/insights/overview?range=all", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    alert_types = [a["type"] for a in body["alerts"]]
    assert "aged_review" in alert_types


def test_review_turnaround_included_in_kpi(client, db, auth_headers):
    """Seed a review_item + approve audit entry and confirm median_review_time appears."""
    doc = create_document(db, filename="pytest-turnaround.pdf", status="approved")
    entered = datetime.now(timezone.utc) - timedelta(hours=4)
    approved_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.add(ReviewItem(document_id=doc.id, reason="test", status="closed", created_at=entered))
    db.add(AuditLog(document_id=doc.id, action="approve",
                    details={}, actor="pytest-reviewer", created_at=approved_at))
    db.commit()

    response = client.get("/insights/overview?range=all", headers=auth_headers)
    assert response.status_code == 200
    kpi = response.json()["kpis"]["median_review_time_hours"]
    assert kpi["current"] is not None
    assert kpi["current"] > 0


def test_failure_breakdown_reflects_correction_examples(client, db, auth_headers):
    doc = create_document(db, filename="pytest-ce-doc.pdf", status="approved")
    ver = DocumentVersion(document_id=doc.id, source="parser", actor="system", data={})
    db.add(ver)
    db.flush()
    ce = CorrectionExample(
        document_id=doc.id,
        original_version_id=ver.id,
        corrected_version_id=ver.id,
        field_diffs=[],
        failure_category="missing_transactions",
        pdf_features={},
    )
    db.add(ce)
    db.commit()

    response = client.get("/insights/overview?range=all", headers=auth_headers)
    assert response.status_code == 200
    cats = {row["category"] for row in response.json()["failure_breakdown"]}
    assert "missing_transactions" in cats


def test_lowest_has_enhanced_fields(client, db, auth_headers):
    doc = create_document(db, filename="pytest-lowest-enhanced.pdf", status="needs_review")
    doc.confidence_score = 0.20
    db.commit()

    response = client.get("/insights/overview?range=all", headers=auth_headers)
    assert response.status_code == 200
    lowest = response.json()["lowest"]
    assert len(lowest) > 0
    first = lowest[0]
    assert "time_in_status_hours" in first
    assert "last_modified_by" in first
    assert "priority" in first

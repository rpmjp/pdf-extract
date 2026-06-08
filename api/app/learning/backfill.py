"""Backfill correction examples from already-approved documents.

This script is operational glue for the continuous-improvement loop. It scans
approved documents that predate the learning pipeline and creates the same
append-only correction-example records that normal approvals create.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..learning.examples import create_correction_example_for_document
from ..models import CorrectionExample, Document


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def backfill() -> dict:
    """Create missing correction examples for approved documents."""

    db = SessionLocal()
    created = 0
    skipped = 0
    try:
        docs = db.query(Document).filter_by(status="approved").order_by(Document.id).all()
        for doc in docs:
            existing = db.query(CorrectionExample).filter_by(document_id=doc.id).first()
            if existing:
                skipped += 1
                continue
            create_correction_example_for_document(db, doc, "backfill")
            created += 1
        db.commit()
        return {"approved_documents": len(docs), "created": created, "skipped": skipped}
    finally:
        db.close()


def main():
    """CLI entry point used from the API container."""

    argparse.ArgumentParser().parse_args()
    print(json.dumps(backfill(), indent=2))


if __name__ == "__main__":
    main()

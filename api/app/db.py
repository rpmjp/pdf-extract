"""Shared SQLAlchemy engine and session factory.

Route handlers, scripts, and Celery workers all create short-lived sessions
from ``SessionLocal``.  ``pool_pre_ping`` prevents stale database connections
from surfacing as confusing failures after container or network restarts.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

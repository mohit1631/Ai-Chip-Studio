"""
app/database.py
-----------------
SQLAlchemy engine + session setup. SQLite by default (matches the Tech
Stack note in 01_phase1_core_platform.md: "SQLite (dev) -> Postgres (prod)").
Swap settings.database_url to a postgres:// URL in prod; nothing else in
this file needs to change.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a DB session, always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

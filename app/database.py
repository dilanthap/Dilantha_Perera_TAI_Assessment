"""SQLite + SQLAlchemy setup.

SQLite is deliberate for a prototype: a single file, zero external services, and
nothing for a reviewer to install or configure before running the app. The
session/engine shape below is standard SQLAlchemy 2.x, so swapping the URL for
Postgres later is a one-line change.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Resolve the DB next to the project root rather than the current working
# directory, so `uvicorn app.main:app` behaves the same from any cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = f"sqlite:///{PROJECT_ROOT / 'policy_sim.db'}"

# check_same_thread=False: FastAPI serves requests from a threadpool, and each
# request gets its own Session, so sharing the connection across threads is safe.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if they don't exist. Called on app startup."""
    from app import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)

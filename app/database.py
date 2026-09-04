"""SQLite (local dev) or Postgres (production) + SQLAlchemy setup.

Local dev stays SQLite by default — a single file, zero external services,
nothing to configure before running. That's fine for a laptop, but it does
NOT work for a Vercel deployment: serverless functions have no persistent
local disk between invocations, so a SQLite file would silently lose data on
whichever cold start doesn't happen to see the previous one's writes — an
upload can succeed and then 404 on the very next request. Set DATABASE_URL
(Neon's Vercel Marketplace integration sets this automatically) to switch to
a real database; anything left unset falls back to local SQLite exactly as
before.
"""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Resolve the DB next to the project root rather than the current working
# directory, so `uvicorn app.main:app` behaves the same from any cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_env_url = os.getenv("DATABASE_URL")

if _env_url:
    # Some providers still hand out the `postgres://` scheme (a Heroku-era
    # convention); SQLAlchemy 2.x rejects it outright. Rewrite to
    # `postgresql://`, then pin the psycopg3 driver explicitly rather than
    # relying on whichever DBAPI happens to be installed.
    if _env_url.startswith("postgres://"):
        _env_url = "postgresql://" + _env_url[len("postgres://") :]
    if _env_url.startswith("postgresql://") and "+psycopg" not in _env_url:
        _env_url = "postgresql+psycopg://" + _env_url[len("postgresql://") :]
    DATABASE_URL = _env_url
    _connect_args: dict = {}
else:
    DATABASE_URL = f"sqlite:///{PROJECT_ROOT / 'policy_sim.db'}"
    # check_same_thread=False: FastAPI serves requests from a threadpool, and
    # each request gets its own Session, so sharing the connection across
    # threads is safe. SQLite-specific — Postgres neither needs nor accepts
    # this argument.
    _connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)

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
    """Create tables if they don't exist. Called on app startup.

    Idempotent (CREATE TABLE IF NOT EXISTS under the hood), so re-running it
    on every serverless cold start against the same Postgres database is
    safe — it's a no-op after the first time.
    """
    from app import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)

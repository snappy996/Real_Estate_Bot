"""Neon Postgres connection helpers (psycopg 3)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from core.config import ROOT, settings

MIGRATIONS = ROOT / "db" / "migrations"


def connect():
    import psycopg

    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and paste the pooled "
            "connection string from your Neon project (AWS Singapore)."
        )
    return psycopg.connect(settings.database_url, autocommit=False)


@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_migrations() -> list[str]:
    """Run every db/migrations/*.sql file in name order. Files are idempotent."""
    applied = []
    with transaction() as conn:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(Path(path).read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied

"""إدارة الاتصال بقاعدة البيانات والجلسات."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session, sessionmaker

from app.database.base import Base, sqlite_engine
from app.database.migrations import run_migrations
from app.utils.logger import get_logger

logger = get_logger("database")

_engine = None
_SessionLocal: sessionmaker | None = None


def init_db(db_path) -> None:
    """تهيئة المحرك وتنفيذ الـ Migration الآمن."""
    global _engine, _SessionLocal
    _engine = sqlite_engine(db_path)
    run_migrations(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    logger.info("Database initialized at %s", db_path)


def get_session_factory() -> sessionmaker:
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _SessionLocal


@contextmanager
def db_session() -> Iterator[Session]:
    """جلسة قصيرة آمنة: commit عند النجاح، rollback عند الخطأ."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _engine


def backup_database(backup_path) -> bool:
    """نسخ احتياطي آمن باستخدام SQLite backup API."""
    import sqlite3

    from app.config import settings

    try:
        src = sqlite3.connect(str(settings.database_path))
        dst = sqlite3.connect(str(backup_path))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        logger.info("Database backed up to %s", backup_path)
        return True
    except Exception as exc:
        logger.error("Backup failed: %s", exc)
        return False

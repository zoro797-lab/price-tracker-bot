"""الأساس المشترك لكل جداول قاعدة البيانات."""
from __future__ import annotations

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, declared_attr


class Base(DeclarativeBase):
    """قاعدة Declarative موحدة مع تسمية تلقائية للجداول."""

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        return "".join(f"_{c.lower()}" if c.isupper() else c for c in cls.__name__).lstrip("_")


def sqlite_engine(db_path):
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )


__all__ = ["Base", "DateTime", "sqlite_engine"]

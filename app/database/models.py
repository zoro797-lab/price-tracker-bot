"""نماذج قاعدة البيانات (SQLAlchemy 2.0 Declarative).

كل الأسعار تُخزن كأرقام عشرية (Float) بعملة الجنيه المصري.
التواريخ تُخزن naive بتوقيت Africa/Cairo لثبات القراءة.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, DateTime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    trackings: Mapped[list["UserTracking"]] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        # منع تكرار نفس المنتج في المتجر الواحد (عند توفر المعرف الخارجي)
        UniqueConstraint("store", "external_product_id", name="uq_product_store_external"),
        Index("ix_products_store", "store"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store: Mapped[str] = mapped_column(String(30), nullable=False)
    external_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="EGP")
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    availability_status: Mapped[str] = mapped_column(String(30), default="unknown")
    data_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserTracking(Base):
    """متابعة مستخدم لمنتج — كل مستخدم له إعداد تنبيه مستقل للمنتج نفسه."""

    __tablename__ = "user_tracking"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_tracking_user_product"),
        Index("ix_tracking_active_product", "is_active", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)

    # --- إعدادات التنبيه ---
    alert_type: Mapped[str] = mapped_column(String(20), default="price_range", nullable=False)
    target_min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_drop_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- حالة التنبيه (منع التكرار) ---
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_armed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_alerted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- تنبيهات التحسن الإضافي ---
    notify_on_additional_drop: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    additional_drop_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="trackings")
    product: Mapped[Product] = relationship()


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_history_product_recorded", "product_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="EGP")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    data_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(30), default="unknown")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_status", "status"),
        Index("ix_notifications_user", "user_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    tracking_id: Mapped[int | None] = mapped_column(ForeignKey("user_tracking.id", ondelete="SET NULL"), nullable=True)
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    price_at_notification: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BotSetting(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    setting_value: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceCheckLog(Base):
    __tablename__ = "price_check_logs"
    __table_args__ = (
        Index("ix_check_logs_product", "product_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

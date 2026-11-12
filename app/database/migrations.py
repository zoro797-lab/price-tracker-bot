"""آلية Migration آمنة لقاعدة البيانات.

المبدأ:
1. إنشاء كل الجداول غير الموجودة (create_all — لا يمس الجداول الموجودة).
2. إضافة الأعمدة الناقصة إلى الجداول الموجودة (ALTER TABLE ADD COLUMN).
3. إنشاء الفهارس الناقصة (CREATE INDEX IF NOT EXISTS).
4. تسجيل رقم إصدار المخطط (schema_version) في جدول bot_settings.

هذا يضمن الحفاظ على كل المتابعات والبيانات القديمة عند ترقية الكود.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database.base import Base
from app.database.models import BotSetting  # noqa: F401 — لضمان تسجيل النماذج
from app.utils.logger import get_logger

logger = get_logger("migrations")

SCHEMA_VERSION = 2

# تعريف الأعمدة التي أضيفت بعد الإصدار الأول، بصيغة (الجدول، الاسم، SQL للنوع الافتراضي).
# تُستخدم عند وجود قاعدة بيانات قديمة ناقصة الأعمدة الجديدة.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "products": {
        "data_source": "VARCHAR(60)",
    },
    "user_tracking": {
        "alert_type": "VARCHAR(20) DEFAULT 'price_range' NOT NULL",
        "target_min_price": "FLOAT",
        "target_max_price": "FLOAT",
        "target_price": "FLOAT",
        "target_drop_percentage": "FLOAT",
        "reference_price": "FLOAT",
        "last_alerted_price": "FLOAT",
        "last_alerted_at": "DATETIME",
        "notify_on_additional_drop": "BOOLEAN DEFAULT 0 NOT NULL",
        "additional_drop_percentage": "FLOAT",
    },
    "notifications": {
        "tracking_id": "INTEGER",
        "price_at_notification": "FLOAT",
    },
    "admin_logs": {
        "admin_id": "BIGINT",
    },
}

# الهجرات التاريخية المسجلة (للتوثيق والتتبع)
MIGRATION_HISTORY = {
    1: "المخطط الأولي: users, products, user_tracking, price_history, notifications, admin_logs, bot_settings, price_check_logs",
    2: "نظام أنواع التنبيه: alert_type/target_min/target_max/target_price/drop_percentage/reference_price + حالة التنبيه ومنع التكرار",
}


def _get_existing_columns(inspector, table: str) -> set[str]:
    try:
        return {col["name"] for col in inspector.get_columns(table)}
    except Exception:
        return set()


def _get_existing_indexes(inspector, table: str) -> set[str]:
    try:
        return {idx["name"] for idx in inspector.get_indexes(table)}
    except Exception:
        return set()


def run_migrations(engine: Engine) -> int:
    """تنفيذ كل خطوات الترقية الآمنة. يُعيد رقم إصدار المخطط بعد الترقية."""
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))

    inspector = inspect(engine)

    # 1) إنشاء الجداول الناقصة فقط
    Base.metadata.create_all(engine)

    # 2) إضافة الأعمدة الناقصة للجداول الموجودة
    existing_tables = set(inspector.get_table_names())
    for table, columns in ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # الجدول لم يُنشأ بعد — create_all أنشأه كاملًا
        existing_cols = _get_existing_columns(inspector, table)
        for col_name, col_sql in columns.items():
            if col_name not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_sql}"))
                logger.info("Migration: added column %s.%s", table, col_name)

    # 3) إنشاء الفهارس الناقصة
    indexes_to_create = [
        ("ix_products_store", "products", "store"),
        ("ix_tracking_active_product", "user_tracking", "is_active, product_id"),
        ("ix_history_product_recorded", "price_history", "product_id, recorded_at"),
        ("ix_notifications_status", "notifications", "status"),
        ("ix_notifications_user", "notifications", "user_id, sent_at"),
        ("ix_check_logs_product", "price_check_logs", "product_id, checked_at"),
        ("ix_users_telegram_id", "users", "telegram_id"),
    ]
    refreshed_inspector = inspect(engine)
    for idx_name, table, columns_sql in indexes_to_create:
        if table not in existing_tables:
            continue
        if idx_name in _get_existing_indexes(refreshed_inspector, table):
            continue
        with engine.begin() as conn:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns_sql})"))
        logger.info("Migration: created index %s", idx_name)

    # 4) تسجيل إصدار المخطط
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bot_settings (setting_key, setting_value, updated_at) "
                "VALUES ('schema_version', :v, :ts) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value = :v, updated_at = :ts"
            ),
            {"v": str(SCHEMA_VERSION), "ts": datetime.utcnow()},
        )
        for version, description in MIGRATION_HISTORY.items():
            conn.execute(
                text(
                    "INSERT INTO bot_settings (setting_key, setting_value, updated_at) "
                    "VALUES (:k, :v, :ts) "
                    "ON CONFLICT(setting_key) DO UPDATE SET setting_value = :v, updated_at = :ts"
                ),
                {"k": f"migration_{version}_applied", "v": description, "ts": datetime.utcnow()},
            )

    logger.info("Schema is up to date (version %s)", SCHEMA_VERSION)
    return SCHEMA_VERSION


def get_schema_version(engine: Engine) -> int:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT setting_value FROM bot_settings WHERE setting_key = 'schema_version'")
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0

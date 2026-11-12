"""إعدادات البوت القابلة للتحكم من لوحة الأدمن (محفوظة في جدول bot_settings)."""
from __future__ import annotations

from app.database import BotSetting, db_session
from app.utils.logger import get_logger

logger = get_logger("settings")

# المفاتيح المدعومة وقيمها الافتراضية وأنواعها
DEFAULT_SETTINGS: dict[str, tuple[str, type]] = {
    "check_interval_hours": ("6", int),
    "max_products_per_user": ("20", int),
    "store_amazon_enabled": ("true", bool),
    "store_noon_enabled": ("true", bool),
    "alerts_enabled": ("true", bool),
    "notable_drop_percentage": ("15.0", float),
    "admin_notifications_enabled": ("true", bool),
    "price_history_retention_days": ("90", int),
    "schema_version": ("2", int),
}

_SETTING_TYPES = {key: vtype for key, (_, vtype) in DEFAULT_SETTINGS.items()}


def get_setting(key: str):
    """قراءة إعداد مع تحويل النوع. يعيد الافتراضي إذا غير موجود."""
    if key not in _SETTING_TYPES:
        raise KeyError(f"Unknown setting: {key}")
    vtype = _SETTING_TYPES[key]
    with db_session() as session:
        row = session.query(BotSetting).filter(BotSetting.setting_key == key).first()
        raw = row.setting_value if row else DEFAULT_SETTINGS[key][0]
    try:
        if vtype is bool:
            return raw.lower() in ("true", "1", "yes", "on")
        if vtype is int:
            return int(float(raw))
        if vtype is float:
            return float(raw)
        return raw
    except (ValueError, TypeError):
        logger.warning("Bad setting value for %s=%r — using default", key, raw)
        default_raw, _ = DEFAULT_SETTINGS[key]
        if vtype is bool:
            return default_raw.lower() in ("true", "1", "yes", "on")
        return vtype(default_raw)


def set_setting(key: str, value) -> bool:
    """كتابة إعداد. يعيد False إذا المفتاح غير معروف."""
    if key not in _SETTING_TYPES:
        return False
    if _SETTING_TYPES[key] is bool:
        raw = "true" if value in (True, "true", "1", 1, "yes", "on") else "false"
    else:
        raw = str(value)
    with db_session() as session:
        row = session.query(BotSetting).filter(BotSetting.setting_key == key).first()
        if row:
            row.setting_value = raw
        else:
            session.add(BotSetting(setting_key=key, setting_value=raw))
    logger.info("Setting updated: %s = %s", key, raw)
    return True


def is_store_enabled(store_key: str) -> bool:
    key = f"store_{store_key}_enabled"
    try:
        return get_setting(key)
    except KeyError:
        return False


def get_all_settings() -> dict:
    return {key: get_setting(key) for key in DEFAULT_SETTINGS if key != "schema_version"}

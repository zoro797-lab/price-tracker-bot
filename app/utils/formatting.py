"""أدوات التنسيق وقراءة الأرقام — تدعم الأرقام العربية والهندية."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings

# تحويل الأرقام الهندية/العربية إلى إنجليزية
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_MONEY_RE = re.compile(r"^\d{1,15}(\.\d{1,2})?$")


def get_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return ZoneInfo("Africa/Cairo")


def now() -> datetime:
    """الوقت الحالي بتوقيت Africa/Cairo (aware)."""
    return datetime.now(tz=get_tz())


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=get_tz())
    return dt.astimezone(get_tz())


def localize(dt: datetime | None) -> datetime | None:
    """إرجاع naive datetime بتوقيت محلي (لتخزينه في SQLite بشكل ثابت)."""
    local = to_local(dt)
    return local.replace(tzinfo=None) if local else None


def parse_number(text: str) -> float | None:
    """قراءة رقم من نص المستخدم. يدعم 3500 ، 3,500 ، 3500.50 ، الأرقام الهندية."""
    if not text:
        return None
    cleaned = text.strip().translate(_DIGIT_MAP)
    cleaned = cleaned.replace(",", "").replace("٫", ".").replace("جنيه", "").strip()
    cleaned = cleaned.rstrip("ج.م").strip()
    if not _MONEY_RE.match(cleaned):
        return None
    value = float(cleaned)
    return value if value >= 0 else None


def format_price(price: float | None, currency: str = "جنيه") -> str:
    if price is None:
        return "غير متوفر"
    return f"{price:,.2f} {currency}".replace(".00 ", " ")


def format_datetime(dt: datetime | None) -> str:
    local = to_local(dt)
    if local is None:
        return "—"
    return local.strftime("%Y-%m-%d %H:%M")


def format_percentage(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def short_text(text: str | None, length: int = 60) -> str:
    if not text:
        return "—"
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 3] + "…"

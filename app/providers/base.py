"""الأساس المشترك لموصلات المتاجر (Provider Architecture).

كل متجر له موصل مستقل يعرف:
- التعرف على رابط المنتج واستخراج المعرف الخارجي.
- جلب بيانات المنتج (الاسم، السعر، الصورة، التوفر) من مصدر مصرح/عام فقط.
- الإبلاغ بصدق عن حالة المصدر — لا بيانات وهمية أبدًا.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("providers")

# User-Agent صريح وشفاف — لا ننتحل هوية متصفح لإخفاء البرنامج، ونوقف أي محاولة عند الحماية.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 PriceTrackerBot/1.0"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """خطأ عام في الموصل."""


class UnsupportedURL(ProviderError):
    """الرابط لا يتبع هذا المتجر."""


class SourceUnavailable(ProviderError):
    """المصدر غير متاح حاليًا (شبكة/تعديل الصفحة/بيانات ناقصة)."""


class BlockedByStore(ProviderError):
    """المتجر أرجع صفحة حماية (CAPTCHA) — نوقف فورًا ولا نحاول التجاوز."""


@dataclass
class ProductSnapshot:
    """قراءة سعر حقيقية واحدة من المصدر."""

    store: str
    title: str
    price: float | None
    currency: str = "EGP"
    image_url: str | None = None
    availability: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    external_id: str | None = None
    data_source: str = "unknown"
    fetched_at: datetime = field(default_factory=datetime.utcnow)


class BaseProvider:
    """الواجهة المشتركة لكل الموصلات."""

    store_key: str = "base"
    store_display_name: str = "غير معروف"
    url_domains: tuple[str, ...] = ()

    def matches_url(self, url: str) -> bool:
        host = extract_host(url)
        if not host:
            return False
        return any(host == d or host.endswith("." + d) for d in self.url_domains)

    def extract_product_id(self, url: str) -> str | None:
        raise NotImplementedError

    def fetch_product(self, url: str) -> ProductSnapshot:
        raise NotImplementedError

    def _http_get(self, url: str) -> str:
        """طلب GET مع مهلة وإعادة محاولة — يفشل بصدق ولا يتجاوز الحماية."""
        last_error: Exception | None = None
        for attempt in range(settings.request_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=_HEADERS,
                    timeout=settings.request_timeout_seconds,
                    allow_redirects=True,
                )
                if response.status_code == 404:
                    raise SourceUnavailable("المنتج غير موجود (404)")
                response.raise_for_status()
                return response.text
            except BlockedByStore:
                raise
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("Fetch attempt %s failed for %s: %s", attempt + 1, url, exc)
        raise SourceUnavailable(f"فشل الوصول للمصدر بعد {settings.request_retries + 1} محاولات: {last_error}")


def extract_host(url: str) -> str | None:
    """استخراج المضيف من رابط — مع حماية أساسية ضد SSRF."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    match = re.match(r"^https?://([^/?#:]+)", url, re.IGNORECASE)
    if not match:
        return None
    host = match.group(1).lower().strip(".")
    # منع عناوين الشبكة الداخلية (حماية SSRF)
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.startswith(("192.168.", "10.", "169.254.")):
        return None
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host):
        return None
    return host


def parse_html_price(text: str) -> float | None:
    """قراءة سعر من نص مثل 'EGP1,234.00' أو '1,234 جنيه' أو '1234'."""
    cleaned = re.sub(r"[^\d.,]", "", text or "")
    cleaned = cleaned.replace(",", "")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
        return value if 0 < value < 100_000_000 else None
    except ValueError:
        return None

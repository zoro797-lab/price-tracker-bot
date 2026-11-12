"""سجل الموصلات — نقطة واحدة للتعرف على المتجر من الرابط وجلب المنتج.

إضافة متجر جديد مستقبلًا = إنشاء Provider جديد وتسجيله في PROVIDERS فقط.
"""
from __future__ import annotations

from app.providers.amazon import AmazonProvider
from app.providers.base import (
    AvailabilityStatus,
    BaseProvider,
    BlockedByStore,
    ProductSnapshot,
    ProviderError,
    SourceUnavailable,
    UnsupportedURL,
    extract_host,
)
from app.providers.noon import NoonProvider
from app.utils.logger import get_logger

logger = get_logger("registry")

PROVIDERS: list[BaseProvider] = [AmazonProvider(), NoonProvider()]

PROVIDERS_BY_KEY: dict[str, BaseProvider] = {p.store_key: p for p in PROVIDERS}

# المتاجر المفعلة من الإعدادات تُدار في settings_service (store_amazon_enabled / store_noon_enabled)


def is_supported_url(url: str) -> bool:
    return any(p.matches_url(url) for p in PROVIDERS)


def get_provider_for_url(url: str) -> BaseProvider:
    for provider in PROVIDERS:
        if provider.matches_url(url):
            return provider
    raise UnsupportedURL("الرابط لا يتبع أي متجر مدعوم (Amazon.eg / Noon Egypt)")


def get_provider(store_key: str) -> BaseProvider | None:
    return PROVIDERS_BY_KEY.get(store_key)


def validate_url_safety(url: str) -> str | None:
    """تحقق أساسي من سلامة الرابط — يعيد رسالة الخطأ أو None إذا سليم."""
    if not url or len(url) > 2048:
        return "الرابط فارغ أو طويل جدًا"
    host = extract_host(url)
    if not host:
        return "الرابط غير صالح — يجب أن يبدأ بـ http:// أو https:// ويشير لموقع عام"
    if not any(p.matches_url(url) for p in PROVIDERS):
        return "المتجر غير مدعوم — المدعوم حاليًا: Amazon.eg و Noon Egypt"
    return None


__all__ = [
    "PROVIDERS",
    "PROVIDERS_BY_KEY",
    "AvailabilityStatus",
    "BaseProvider",
    "BlockedByStore",
    "ProductSnapshot",
    "ProviderError",
    "SourceUnavailable",
    "UnsupportedURL",
    "extract_host",
    "get_provider",
    "get_provider_for_url",
    "is_supported_url",
    "validate_url_safety",
]

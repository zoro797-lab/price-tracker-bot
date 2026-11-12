"""موصل Amazon.eg.

المصدر: صفحة المنتج العامة على amazon.eg (قراءة HTML العام فقط).
- لا نتجاوز CAPTCHA ولا أنظمة الحماية — عند اكتشاف صفحة حماية نُبلغ بأن المصدر محجوب.
- لا نعرض أسعارًا وهمية: إذا لم نتمكن من قراءة سعر حقيقي نُعلن الفشل.
"""
from __future__ import annotations

import re

from app.providers.base import (
    AvailabilityStatus,
    BaseProvider,
    BlockedByStore,
    ProductSnapshot,
    SourceUnavailable,
    parse_html_price,
)

# أنماط استخراج البيانات من صفحة Amazon العامة
_TITLE_RE = re.compile(r'<span id="productTitle"[^>]*>\s*([^<]+?)\s*</span>', re.DOTALL)
_PRICE_PATTERNS = [
    re.compile(r'<span class="a-offscreen">\s*EGP\s*([\d.,]+)\s*</span>'),
    re.compile(r'<span class="a-offscreen">\s*([\d.,]+)\s*(?:EGP|ج\.?م|جنيه)\s*</span>'),
    re.compile(r'id="priceblock_ourprice"[^>]*>\s*EGP?\s*([\d.,]+)'),
]
_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')
_LANDING_IMAGE_RE = re.compile(r'id="landingImage"[^>]+data-old-harness[^"]*"([^"]+)"')
_AVAILABILITY_RE = re.compile(r'id="availability"[^>]*>\s*.*?(In Stock|out of stock)', re.IGNORECASE | re.DOTALL)
_CAPTCHA_MARKERS = ("api-services-support@amazon.com", "captcha-delivery", "/errors/validateCaptcha")


class AmazonProvider(BaseProvider):
    store_key = "amazon"
    store_display_name = "Amazon.eg"
    url_domains = ("amazon.eg",)

    def extract_product_id(self, url: str) -> str | None:
        # /dp/ASIN أو /gp/product/ASIN — ASIN = 10 أحرف/أرقام
        match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", url, re.IGNORECASE)
        return match.group(1).upper() if match else None

    def fetch_product(self, url: str) -> ProductSnapshot:
        external_id = self.extract_product_id(url)
        if not external_id:
            raise SourceUnavailable("لم يتمكن الموصل من استخراج معرف المنتج (ASIN) من الرابط")

        html = self._http_get(url)

        for marker in _CAPTCHA_MARKERS:
            if marker in html:
                raise BlockedByStore("Amazon أرجع صفحة حماية (CAPTCHA) — لن نحاول التجاوز")

        title_match = _TITLE_RE.search(html)
        if not title_match:
            raise SourceUnavailable("تعذر قراءة اسم المنتج من الصفحة (قد يكون تصميم الصفحة تغيّر)")

        price: float | None = None
        for pattern in _PRICE_PATTERNS:
            price_match = pattern.search(html)
            if price_match:
                price = parse_html_price(price_match.group(1))
                if price:
                    break

        image_match = _IMAGE_RE.search(html) or _LANDING_IMAGE_RE.search(html)
        availability = AvailabilityStatus.UNKNOWN
        if _AVAILABILITY_RE.search(html):
            availability = (
                AvailabilityStatus.AVAILABLE if "in stock" in _AVAILABILITY_RE.search(html).group(1).lower()
                else AvailabilityStatus.UNAVAILABLE
            )

        if price is None:
            # منتج موجود لكن السعر غير مقروء — لا نتخيل سعرًا
            raise SourceUnavailable("تعذر قراءة السعر الحقيقي من الصفحة")

        return ProductSnapshot(
            store=self.store_key,
            title=" ".join(title_match.group(1).split()),
            price=price,
            currency="EGP",
            image_url=image_match.group(1) if image_match else None,
            availability=availability,
            external_id=external_id,
            data_source="amazon_public_page",
        )

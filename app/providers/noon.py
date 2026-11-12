"""موصل Noon Egypt.

المصدر: صفحة المنتج العامة على noon.com (قراءة HTML/JSON-LD العام فقط).
- لا نتجاوز الحماية ولا نستخدم بيانات وهمية.
- صفحات Noon تضم بيانات المنتج في JSON-LD داخل الصفحة.
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote

from app.providers.base import (
    AvailabilityStatus,
    BaseProvider,
    ProductSnapshot,
    SourceUnavailable,
    parse_html_price,
)

_TITLE_RE = re.compile(r'<title[^>]*>\s*([^<]+?)\s*</title>', re.DOTALL)
_JSONLD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)
_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')
_IN_STOCK_RE = re.compile(r'"availability"\s*:\s*"(https://schema\.org/)?(InStock|OutOfStock)"', re.IGNORECASE)


class NoonProvider(BaseProvider):
    store_key = "noon"
    store_display_name = "Noon Egypt"
    url_domains = ("noon.com",)

    def extract_product_id(self, url: str) -> str | None:
        # نمط noon: /egypt-en/<slug>/p/<SKU>/ — قد يكون SKU مشفرًا URL-encoding
        match = re.search(r"/p/([A-Za-z0-9%_-]+)/?", url)
        if not match:
            return None
        return unquote(match.group(1)).upper()

    def _is_egypt(self, url: str) -> bool:
        return bool(re.search(r"noon\.com/(egypt-en|ar-eg)", url, re.IGNORECASE))

    def fetch_product(self, url: str) -> ProductSnapshot:
        external_id = self.extract_product_id(url)
        if not external_id:
            raise SourceUnavailable("لم يتمكن الموصل من استخراج معرف المنتج (SKU) من الرابط")

        html = self._http_get(url)

        # البحث في JSON-LD عن بيانات المنتج والسعر الحقيقي
        price: float | None = None
        title: str | None = None
        image_url: str | None = None
        availability = AvailabilityStatus.UNKNOWN

        for block in _JSONLD_RE.findall(html):
            try:
                data = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") == "Product":
                    title = title or item.get("name")
                    image_url = image_url or (item.get("image") if isinstance(item.get("image"), str) else None)
                    offers = item.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price_str = offers.get("price")
                    if price_str and price is None:
                        price = parse_html_price(str(price_str))

        if title is None:
            title_match = _TITLE_RE.search(html)
            if title_match:
                title = title_match.group(1).split("|")[0].strip()

        if image_url is None:
            image_match = _IMAGE_RE.search(html)
            image_url = image_match.group(1) if image_match else None

        stock_match = _IN_STOCK_RE.search(html)
        if stock_match:
            availability = (
                AvailabilityStatus.AVAILABLE if stock_match.group(2).lower() == "instock"
                else AvailabilityStatus.UNAVAILABLE
            )

        if price is None:
            raise SourceUnavailable("تعذر قراءة السعر الحقيقي من صفحة Noon")

        if not title:
            raise SourceUnavailable("تعذر قراءة اسم المنتج من صفحة Noon")

        return ProductSnapshot(
            store=self.store_key,
            title=" ".join(title.split()),
            price=price,
            currency="EGP",
            image_url=image_url,
            availability=availability,
            external_id=external_id,
            data_source="noon_public_page",
        )

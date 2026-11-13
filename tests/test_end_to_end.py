"""اختبار نهاية-إلى-نهاية: دورة فحص مجدولة كاملة بمزود وهمي + رسوم بيانية + استيراد البوت.

يحاكي جدول سلوك المواصفات كاملًا عبر check_single_product كما تعمل في الإنتاج:
5000 → لا إشعار | 3900 → إشعار | 3800 → لا تكرار | 4100 → إعادة تسليح | 3950 → إشعار جديد.
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

import app.services.price_check_service as pcs
from app.database import db_session
from app.providers.base import AvailabilityStatus, ProductSnapshot
from app.services import price_history_service, tracking_service, user_service
from app.services.product_service import get_or_create_product
from app.services.tracking_service import create_tracking


class FakeProvider:
    """مزود وهمي يرجع سعرًا مبرمجًا مسبقًا (بيانات وهمية للاختبار فقط)."""

    store_key = "amazon"
    store_display_name = "Amazon.eg (Mock)"

    def __init__(self, prices: list[float]):
        self.prices = list(prices)
        self.calls = 0

    def fetch_product(self, url: str) -> ProductSnapshot:
        price = self.prices[min(self.calls, len(self.prices) - 1)]
        self.calls += 1
        return ProductSnapshot(
            store=self.store_key,
            title="منتج نهاية-إلى-نهاية",
            price=price,
            availability=AvailabilityStatus.AVAILABLE,
            external_id="E2E0001",
            data_source="mock",
        )


@pytest.fixture()
def e2e_setup(sample_user, fake_bot, monkeypatch):
    """منتج + متابعة نطاق 3500-4000 + حقن المزود الوهمي في خدمة الفحص."""
    snapshot = ProductSnapshot(
        store="amazon", title="منتج نهاية-إلى-نهاية", price=5000,
        availability=AvailabilityStatus.AVAILABLE,
        external_id=f"E2E{time.time_ns() % 10**8}", data_source="mock",
    )
    product, is_new = get_or_create_product(snapshot, "https://www.amazon.eg/dp/E2E")
    tracking = create_tracking(
        user=sample_user, product=product,
        alert_type="price_range", target_min_price=3500, target_max_price=4000,
    )

    provider = FakeProvider(prices=[])
    monkeypatch.setattr(pcs, "get_provider", lambda store: provider)

    from app.services import settings_service
    monkeypatch.setattr(settings_service, "is_store_enabled", lambda store: True)
    # نستدعي الدوال الأصلية للإعدادات كما هي

    return product, tracking, provider, fake_bot


class TestScheduledCheckEndToEnd:
    async def test_full_alert_lifecycle_through_scheduler(self, e2e_setup):
        product, tracking, provider, bot = e2e_setup

        # --- الفحص 1: سعر 5000 (أعلى من النطاق) → لا إشعار ---
        provider.prices = [5000]
        price = await pcs.check_single_product(bot, product)
        assert price == 5000
        assert len(bot.sent_messages) == 0
        state = tracking_service.get_tracking(tracking.id)
        assert state.alert_armed is True
        # القراءة سُجلت في السجل التاريخي
        assert price_history_service.get_stats(product.id, days=30).readings_count >= 1

        # --- الفحص 2: سعر 3900 (داخل النطاق) → إشعار ---
        provider.prices = [3900]
        price = await pcs.check_single_product(bot, product)
        assert price == 3900
        assert len(bot.sent_messages) == 1
        alert_text = bot.sent_messages[0]["text"]
        assert "🎉" in alert_text
        assert "3,900" in alert_text
        assert "3,500" in alert_text and "4,000" in alert_text
        state = tracking_service.get_tracking(tracking.id)
        assert state.alert_armed is False
        assert state.last_alerted_price == 3900
        # الإشعار مسجل في جدول notifications بحالة sent
        notifications = pcs.recent_notifications_list(product.id) if hasattr(pcs, "recent_notifications_list") else []
        from app.services.notification_service import recent_notifications
        rows = recent_notifications(limit=5)
        assert any(r.price_at_notification == 3900 and r.status == "sent" for r in rows)

        # --- الفحص 3: سعر 3800 (الشرط ما زال متحققًا) → لا تكرار ---
        provider.prices = [3800]
        await pcs.check_single_product(bot, product)
        assert len(bot.sent_messages) == 1  # لا رسالة جديدة

        # --- الفحص 3.5: سعر 3000 (أقل من الحد الأدنى) → ما زال لا تكرار ---
        provider.prices = [3000]
        await pcs.check_single_product(bot, product)
        assert len(bot.sent_messages) == 1

        # --- الفحص 4: سعر 4100 (ارتفاع فوق النطاق) → إعادة تسليح بدون إشعار ---
        provider.prices = [4100]
        await pcs.check_single_product(bot, product)
        assert len(bot.sent_messages) == 1
        state = tracking_service.get_tracking(tracking.id)
        assert state.alert_armed is True

        # --- الفحص 5: سعر 3950 (انخفض مجددًا) → إشعار جديد ---
        provider.prices = [3950]
        await pcs.check_single_product(bot, product)
        assert len(bot.sent_messages) == 2
        second_alert = bot.sent_messages[1]["text"]
        assert "3,950" in second_alert
        state = tracking_service.get_tracking(tracking.id)
        assert state.alert_armed is False
        assert state.last_alerted_price == 3950

    async def test_product_update_and_history_from_checks(self, e2e_setup):
        product, tracking, provider, bot = e2e_setup
        provider.prices = [4200, 4100, 3900]
        for _ in range(3):
            await pcs.check_single_product(bot, product)
        from app.services.product_service import get_product
        updated = get_product(product.id)
        assert updated.current_price == 3900
        assert updated.last_checked_at is not None
        stats = price_history_service.get_stats(product.id, days=30)
        assert stats.readings_count >= 3
        assert stats.min_price == 3900

    async def test_failed_check_does_not_stop_system(self, e2e_setup, monkeypatch):
        """فشل جلب السعر → سجل فشل + لا إشعار، والنظام يكمل."""
        product, tracking, provider, bot = e2e_setup
        from app.providers.base import SourceUnavailable

        def failing_fetch(url):
            raise SourceUnavailable("المصدر محجوب (اختبار)")

        provider.fetch_product = failing_fetch
        price = await pcs.check_single_product(bot, product)
        assert price is None
        assert len(bot.sent_messages) == 0
        # سجل الفحص يسجل الفشل
        assert pcs.get_consecutive_failures(product.id) >= 1

    async def test_alerts_disabled_stops_notifications(self, e2e_setup, monkeypatch):
        product, tracking, provider, bot = e2e_setup
        from app.services import settings_service

        provider.prices = [3900]
        monkeypatch.setattr(settings_service, "get_setting", lambda key, *a, **k: False if key == "alerts_enabled" else 6)
        await pcs.check_single_product(bot, product)
        assert len(bot.sent_messages) == 0


class TestChartsAndImport:
    def test_chart_generation(self, sample_user):
        """إنشاء رسم بياني لمنتج له قراءات مع تظليل النطاق المستهدف."""
        import tempfile
        from pathlib import Path

        snapshot = ProductSnapshot(
            store="amazon", title="منتج رسم بياني", price=5000,
            availability=AvailabilityStatus.AVAILABLE,
            external_id=f"CHRT{time.time_ns() % 10**8}", data_source="mock",
        )
        product, _ = get_or_create_product(snapshot, "https://www.amazon.eg/dp/CHRT")
        with db_session() as session:
            from app.database.models import PriceHistory
            from app.utils.formatting import localize, now
            base = localize(now())
            for i, price in enumerate([5000, 4800, 4600, 4400, 4100]):
                session.add(PriceHistory(
                    product_id=product.id, price=price,
                    recorded_at=base - timedelta(hours=(5 - i) * 12),
                ))
        tracking = create_tracking(
            user=sample_user, product=product,
            alert_type="price_range", target_min_price=3500, target_max_price=4000,
        )
        from app.utils.charts import build_price_chart
        chart_path = build_price_chart(tracking, days=7)
        assert chart_path is not None
        assert Path(chart_path).exists()
        assert Path(chart_path).stat().st_size > 10000  # صورة فعلية غير فارغة
        Path(chart_path).unlink(missing_ok=True)

    def test_dispatcher_builds_and_routers_register(self, dispatcher):
        """كل الراوترات تسجل بدون تعارضات — smoke test لاستيراد البوت.

        يستخدم fixture الـ dispatcher المشترك حتى لا تُربط الراوترات مرتين.
        """
        assert dispatcher is not None
        # عدد المعالجات المُسجلة > 0
        assert len(dispatcher.sub_routers) == 4

    def test_url_validation_and_providers(self):
        """التعرف على الروابط وحماية SSRF."""
        from app.providers.registry import get_provider_for_url, validate_url_safety
        assert get_provider_for_url("https://www.amazon.eg/dp/B0ABCDE123").store_key == "amazon"
        assert get_provider_for_url("https://www.noon.com/egypt-en/product/p/ABC123/").store_key == "noon"
        # رابط غير مدعوم
        assert validate_url_safety("https://jumia.com.eg/product") is not None
        # محاولة SSRF
        assert validate_url_safety("http://localhost/dp/X") is not None
        assert validate_url_safety("http://192.168.1.1/dp/X") is not None
        assert validate_url_safety("http://169.254.169.254/latest/meta-data") is not None
        # رابط سليم
        assert validate_url_safety("https://www.amazon.eg/dp/B0ABCDE123") is None

    def test_amazon_asin_extraction(self):
        from app.providers.registry import get_provider_for_url
        provider = get_provider_for_url("https://www.amazon.eg/-/dp/B0ABCDE123?ref=x")
        assert provider.extract_product_id("https://www.amazon.eg/-/dp/B0ABCDE123?ref=x") == "B0ABCDE123"

    def test_noon_sku_extraction(self):
        from app.providers.registry import get_provider_for_url
        provider = get_provider_for_url("https://www.noon.com/egypt-en/some-product/p/ZXCV1234/")
        assert provider.extract_product_id("https://www.noon.com/egypt-en/some-product/p/ZXCV1234/") == "ZXCV1234"

    def test_number_parsing_arabic_digits(self):
        from app.utils.formatting import parse_number, format_price
        assert parse_number("3500") == 3500
        assert parse_number("3,500") == 3500
        assert parse_number("٣٥٠٠") == 3500
        assert parse_number("3500.50") == 3500.5
        assert parse_number("٤٥٦٧") == 4567
        assert parse_number("abc") is None
        assert parse_number("-500") is None
        assert "3,500" in format_price(3500)

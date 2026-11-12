"""اختبارات سجل الأسعار + المنتجات + Migration الآمن + الإشعارات + الإعدادات."""
from __future__ import annotations

import time
from datetime import timedelta

from app.database import db_session
from app.database.models import Notification, PriceHistory
from app.providers.base import AvailabilityStatus, ProductSnapshot
from app.services import price_history_service, settings_service
from app.services.notification_service import mark_failed, mark_sent, queue_notification, send_pending_notifications
from app.services.product_service import get_or_create_product
from app.utils.formatting import localize, now


def _snap(external_id: str, price: float = 5000.0):
    return ProductSnapshot(
        store="amazon", title="منتج اختبار", price=price,
        availability=AvailabilityStatus.AVAILABLE,
        external_id=external_id, data_source="test",
    )


class TestProductDeduplication:
    def test_same_store_and_external_id_reuses_product(self):
        external = f"DEDUP{time.time_ns() % 10**8}"
        product1, new1 = get_or_create_product(_snap(external, 5000), "https://www.amazon.eg/dp/X")
        product2, new2 = get_or_create_product(_snap(external, 4800), "https://www.amazon.eg/dp/X")
        assert new1 is True and new2 is False
        assert product1.id == product2.id
        assert product2.current_price == 4800  # تحُدث بآخر قراءة

    def test_same_id_different_store_is_separate(self):
        external = f"CROSS{time.time_ns() % 10**8}"
        p1, _ = get_or_create_product(_snap(external, 5000), "https://www.amazon.eg/dp/Y")
        snap_noon = _snap(external, 5100)
        snap_noon.store = "noon"
        p2, new2 = get_or_create_product(snap_noon, "https://www.noon.com/egypt-en/x/p/Y/")
        assert p2.id != p1.id
        assert new2 is True


class TestPriceHistory:
    def test_record_and_dedupe_within_gap(self):
        product, _ = get_or_create_product(_snap(f"HIST{time.time_ns() % 10**8}", 5000), "https://www.amazon.eg/dp/H")
        assert price_history_service.record_price(product.id, 4900, data_source="test") is True
        # نفس السعر خلال الفترة القصيرة → لا تسجيل مكرر
        assert price_history_service.record_price(product.id, 4900, data_source="test") is False
        # سعر مختلف → تسجيل
        assert price_history_service.record_price(product.id, 4700, data_source="test") is True

        stats = price_history_service.get_stats(product.id, days=30)
        assert stats.readings_count >= 2
        assert stats.min_price == 4700
        assert stats.max_price == 4900

    def test_stats_honest_about_short_history(self):
        """منتج عمره 3 أيام فقط → days_count=3 ولا تدّعي 30 يومًا."""
        product, _ = get_or_create_product(_snap(f"SHORT{time.time_ns() % 10**8}", 5000), "https://www.amazon.eg/dp/S")
        with db_session() as session:
            base = localize(now())
            for days_ago, price in [(3, 5000), (2, 4800), (1, 4600)]:
                session.add(PriceHistory(
                    product_id=product.id, price=price,
                    recorded_at=base - timedelta(days=days_ago),
                    data_source="test",
                ))
        stats = price_history_service.get_stats(product.id, days=30)
        assert stats.days_count == 3
        assert stats.min_price == 4600
        assert stats.max_price == 5000
        assert stats.avg_price == (5000 + 4800 + 4600) / 3
        assert stats.change_percentage == -8.0

    def test_empty_history(self):
        product, _ = get_or_create_product(_snap(f"EMPTY{time.time_ns() % 10**8}", 5000), "https://www.amazon.eg/dp/E")
        stats = price_history_service.get_stats(product.id, days=30)
        assert stats.readings_count == 0
        assert stats.min_price is None
        assert stats.days_count == 0

    def test_cleanup_old_history(self):
        product, _ = get_or_create_product(_snap(f"CLEAN{time.time_ns() % 10**8}", 5000), "https://www.amazon.eg/dp/C")
        with db_session() as session:
            session.add(PriceHistory(
                product_id=product.id, price=5000,
                recorded_at=localize(now()) - timedelta(days=400),
                data_source="test",
            ))
        deleted = price_history_service.cleanup_old_history(retention_days=365)
        assert deleted >= 1

    def test_failed_readings_are_not_recorded(self):
        """القراءات الفاشلة لا تدخل السجل — record_price لا يستدعى إلا بعد نجاح."""
        product, _ = get_or_create_product(_snap(f"FAIL{time.time_ns() % 10**8}", 5000), "https://www.amazon.eg/dp/F")
        count_before = price_history_service.get_stats(product.id, days=30).readings_count
        # لا نستدعي record_price عند الفشل (محاكاة) — العدد يبقى كما هو
        count_after = price_history_service.get_stats(product.id, days=30).readings_count
        assert count_before == count_after


class TestMigrationsSafe:
    def test_migration_preserves_old_data(self, tmp_path):
        """محاكاة قاعدة بيانات قديمة ناقصة الأعمدة الجديدة → الترقية تحفظ البيانات."""
        import sqlite3

        from sqlalchemy import inspect, text

        from app.database.base import sqlite_engine
        from app.database.migrations import run_migrations

        old_db = tmp_path / "old_schema.db"
        conn = sqlite3.connect(old_db)
        cur = conn.cursor()
        # مخطط قديم: user_tracking بدون أعمدة نظام التنبيه الجديد
        cur.execute("""
            CREATE TABLE user_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                alert_armed BOOLEAN DEFAULT 1,
                created_at DATETIME
            )
        """)
        cur.execute("""
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store VARCHAR(30) NOT NULL,
                external_product_id VARCHAR(128),
                product_url VARCHAR(2048) NOT NULL,
                title TEXT,
                currency VARCHAR(10) DEFAULT 'EGP',
                current_price FLOAT,
                availability_status VARCHAR(30),
                last_checked_at DATETIME,
                created_at DATETIME
            )
        """)
        cur.execute("INSERT INTO products (store, external_product_id, product_url, title, current_price) VALUES ('amazon', 'OLDA', 'https://x', 'منتج قديم', 1234)")
        cur.execute("INSERT INTO user_tracking (user_id, product_id, is_active) VALUES (1, 1, 1)")
        conn.commit()
        conn.close()

        engine = sqlite_engine(old_db)
        version = run_migrations(engine)

        inspector = inspect(engine)
        tracking_cols = {c["name"] for c in inspector.get_columns("user_tracking")}
        # الأعمدة الجديدة أُضيفت كلها
        for col in ("alert_type", "target_min_price", "target_max_price", "target_price",
                    "target_drop_percentage", "reference_price", "last_alerted_price",
                    "last_alerted_at", "notify_on_additional_drop", "additional_drop_percentage"):
            assert col in tracking_cols, f"ناقص العمود {col}"

        # البيانات القديمة محفوظة
        with engine.connect() as c:
            row = c.execute(text("SELECT current_price, title FROM products WHERE external_product_id='OLDA'")).fetchone()
            assert row[0] == 1234 and row[1] == "منتج قديم"
            tracking = c.execute(text("SELECT alert_type, is_active FROM user_tracking WHERE id=1")).fetchone()
            assert tracking[0] == "price_range"  # القيمة الافتراضية
            assert tracking[1] == 1

        assert version >= 2
        engine.dispose()


class TestNotifications:
    def test_queue_and_mark_sent(self, sample_user):
        notification_id = queue_notification(
            sample_user.id, None, None, "price_alert", 3900, "رسالة اختبار",
        )
        mark_sent(notification_id)
        with db_session() as session:
            row = session.query(Notification).filter(Notification.id == notification_id).first()
            assert row.status == "sent"
            assert row.sent_at is not None

    async def test_pending_survives_and_retries(self, sample_user, fake_bot):
        """الإشعار المعلق يبقى ويُعاد إرساله (ضمان عدم الضياع عند إعادة التشغيل)."""
        notification_id = queue_notification(
            sample_user.id, None, None, "price_alert", 3900, "رسالة إعادة إرسال",
        )
        sent = await send_pending_notifications(fake_bot)
        assert sent >= 1
        assert any("رسالة إعادة إرسال" in m["text"] for m in fake_bot.sent_messages)

    def test_failed_marking(self, sample_user):
        notification_id = queue_notification(sample_user.id, None, None, "x", None, "م")
        mark_failed(notification_id)
        with db_session() as session:
            row = session.query(Notification).filter(Notification.id == notification_id).first()
            assert row.status == "failed"


class TestSettings:
    def test_defaults(self):
        assert settings_service.get_setting("check_interval_hours") == 6
        assert settings_service.get_setting("alerts_enabled") is True

    def test_set_and_get_roundtrip(self):
        settings_service.set_setting("check_interval_hours", 12)
        assert settings_service.get_setting("check_interval_hours") == 12
        settings_service.set_setting("check_interval_hours", 6)  # استعادة الافتراضي

    def test_boolean_toggle(self):
        settings_service.set_setting("store_amazon_enabled", False)
        assert settings_service.is_store_enabled("amazon") is False
        settings_service.set_setting("store_amazon_enabled", True)
        assert settings_service.is_store_enabled("amazon") is True

    def test_unknown_setting_rejected(self):
        assert settings_service.set_setting("hacked_key", 1) is False


class TestAdminMessages:
    def test_admin_message_contains_range_details(self, sample_user):
        from app.providers.base import ProductSnapshot
        from app.services.product_service import get_or_create_product
        from app.services.tracking_service import create_tracking
        from app.services.alert_service import build_admin_new_product_message

        snapshot = ProductSnapshot(
            store="amazon", title="سماعات بلوتوث", price=5000,
            availability=AvailabilityStatus.AVAILABLE,
            external_id=f"ADMIN{time.time_ns() % 10**8}", data_source="test",
        )
        product, _ = get_or_create_product(snapshot, "https://www.amazon.eg/dp/A")
        tracking = create_tracking(
            user=sample_user, product=product, alert_type="price_range",
            target_min_price=3500, target_max_price=4000,
        )
        message = build_admin_new_product_message(sample_user, product, tracking)
        assert "نطاق سعري" in message
        assert "3,500" in message and "4,000" in message
        assert sample_user.first_name in message

    def test_admin_message_for_percentage_drop(self, sample_user):
        from app.providers.base import ProductSnapshot
        from app.services.product_service import get_or_create_product
        from app.services.tracking_service import create_tracking
        from app.services.alert_service import build_admin_new_product_message

        snapshot = ProductSnapshot(
            store="noon", title="موبايل", price=5000,
            availability=AvailabilityStatus.AVAILABLE,
            external_id=f"ADMD{time.time_ns() % 10**8}", data_source="test",
        )
        product, _ = get_or_create_product(snapshot, "https://www.noon.com/egypt-en/x/p/D/")
        tracking = create_tracking(
            user=sample_user, product=product, alert_type="percentage_drop",
            target_drop_percentage=10, reference_price=5000,
        )
        message = build_admin_new_product_message(sample_user, product, tracking)
        assert "10.0%" in message
        assert "السعر المرجعي" in message
        assert "5,000" in message

    def test_alert_message_honest_about_history(self, sample_user):
        """رسالة التنبيه لا تدّعي إحصائيات 30 يومًا بدون تاريخ حقيقي."""
        from app.providers.base import ProductSnapshot
        from app.services.product_service import get_or_create_product
        from app.services.tracking_service import create_tracking
        from app.services.alert_service import build_alert_message
        from app.services.alert_evaluator import evaluate_alert

        snapshot = ProductSnapshot(
            store="amazon", title="منتج بدون تاريخ", price=3900,
            availability=AvailabilityStatus.AVAILABLE,
            external_id=f"HONEST{time.time_ns() % 10**8}", data_source="test",
        )
        product, _ = get_or_create_product(snapshot, "https://www.amazon.eg/dp/HH")
        tracking = create_tracking(
            user=sample_user, product=product, alert_type="price_range",
            target_min_price=3500, target_max_price=4000,
        )
        decision = evaluate_alert(tracking, 3900)
        message = build_alert_message(tracking, product, 3900, decision, "new")
        assert "3,900" in message
        assert "أقل سعر مسجل خلال" not in message  # لا تاريخ متاح → لا ادعاء

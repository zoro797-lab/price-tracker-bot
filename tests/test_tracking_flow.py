"""اختبارات تدفق التنبيه على قاعدة البيانات الحقيقية — منع التكرار الذري والحالة."""
from __future__ import annotations

import time

from app.providers.base import AvailabilityStatus, ProductSnapshot
from app.services import alert_service, tracking_service, user_service
from app.services.product_service import get_or_create_product


def _make_product(url_suffix: str = "", price: float = 5000.0):
    snapshot = ProductSnapshot(
        store="amazon",
        title=f"منتج اختبار {url_suffix or time.time_ns()}",
        price=price,
        availability=AvailabilityStatus.AVAILABLE,
        external_id=f"TEST{time.time_ns() % 10**8}{url_suffix}",
        data_source="test",
    )
    url = f"https://www.amazon.eg/dp/{snapshot.external_id}"
    product, is_new = get_or_create_product(snapshot, url)
    return product, is_new


def _make_range_tracking(user, product, min_price=3500, max_price=4000):
    return tracking_service.create_tracking(
        user=user,
        product=product,
        alert_type="price_range",
        target_min_price=min_price,
        target_max_price=max_price,
    )


class TestTrackingLifecycleOnDatabase:
    def test_first_check_above_range_no_alert(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        decision, claimed = alert_service.evaluate_and_claim(tracking, 5000)
        assert decision.should_alert is False
        assert claimed is False

        refreshed = tracking_service.get_tracking(tracking.id)
        assert refreshed.alert_armed is True
        assert refreshed.last_alerted_price is None

    def test_alert_at_max_boundary_records_state(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        decision, claimed = alert_service.evaluate_and_claim(tracking, 4000)
        assert claimed is True

        refreshed = tracking_service.get_tracking(tracking.id)
        assert refreshed.alert_armed is False
        assert refreshed.last_alerted_price == 4000
        assert refreshed.last_alerted_at is not None

    def test_no_repeat_while_condition_met(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        assert alert_service.evaluate_and_claim(tracking, 3900)[1] is True
        # نفس الشرط ما زال متحققًا — لا تكرار حتى عند سعر أقل من الحد الأدنى
        assert alert_service.evaluate_and_claim(tracking, 3500)[1] is False
        assert alert_service.evaluate_and_claim(tracking, 3000)[1] is False

        refreshed = tracking_service.get_tracking(tracking.id)
        assert refreshed.alert_armed is False
        assert refreshed.last_alerted_price == 3900

    def test_rearm_then_new_alert(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        # 4000: إشعار أول
        assert alert_service.evaluate_and_claim(tracking, 4000)[1] is True
        # 4100: ارتفاع → إعادة تسليح (بدون إشعار) — قراءة جديدة كما في كل دورة فحص
        refreshed = tracking_service.get_tracking(tracking.id)
        decision, claimed = alert_service.evaluate_and_claim(refreshed, 4100)
        assert claimed is False and decision.rearm is True
        refreshed = tracking_service.get_tracking(tracking.id)
        assert refreshed.alert_armed is True
        # 3950: انخفاض → إشعار جديد
        decision, claimed = alert_service.evaluate_and_claim(refreshed, 3950)
        assert claimed is True and decision.alert_kind == "new"

    def test_price_below_min_alerts_on_first_check(self, sample_user):
        """حالة مهمة: أول فحص والسعر أصلاً أقل من الحد الأدنى → إشعار."""
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        decision, claimed = alert_service.evaluate_and_claim(tracking, 2500)
        assert claimed is True
        assert decision.price_status.value == "below_range"

    def test_initial_alert_on_creation_when_condition_met(self, sample_user):
        """عند إضافة منتج سعره داخل النطاق من البداية → إشعار فوري (أول تحقق)."""
        product, _ = _make_product(price=3900)
        tracking = _make_range_tracking(sample_user, product)

        decision, message = alert_service.evaluate_initial_alert(tracking, product, 3900)
        assert message != ""  # رسالة إشعار جاهزة
        assert "3,900" in message

        refreshed = tracking_service.get_tracking(tracking.id)
        assert refreshed.alert_armed is False
        assert refreshed.last_alerted_price == 3900

    def test_no_initial_alert_when_above_range(self, sample_user):
        product, _ = _make_product(price=5000)
        tracking = _make_range_tracking(sample_user, product)
        _, message = alert_service.evaluate_initial_alert(tracking, product, 5000)
        assert message == ""
        assert tracking_service.get_tracking(tracking.id).alert_armed is True


class TestIndependentUsers:
    def test_two_users_same_product_independent_states(self, sample_user):
        """كل مستخدم له إعداد تنبيه وحالة مستقلة لنفس المنتج."""
        product, _ = _make_product()
        user2 = user_service.get_or_create_user(999999999001, "Second", "User")[0]

        tracking1 = _make_range_tracking(sample_user, product, min_price=3500, max_price=4000)
        tracking2 = _make_range_tracking(user2, product, min_price=3000, max_price=3600)

        # سعر 3900: يحقق شرط المستخدم الأول فقط
        assert alert_service.evaluate_and_claim(tracking1, 3900)[1] is True
        assert alert_service.evaluate_and_claim(tracking2, 3900)[1] is False

        t1 = tracking_service.get_tracking(tracking1.id)
        t2 = tracking_service.get_tracking(tracking2.id)
        assert t1.alert_armed is False
        assert t2.alert_armed is True

        # سعر 3400: يحقق شرط الثاني (3300-3600) والأول أيضًا (< 3500 لكن غير مسلح)
        assert alert_service.evaluate_and_claim(t2, 3400)[1] is True
        assert alert_service.evaluate_and_claim(t1, 3400)[1] is False

    def test_stop_resume_rearms(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        assert alert_service.evaluate_and_claim(tracking, 3900)[1] is True
        # إيقاف: لا إشعارات
        tracking_service.set_tracking_active(tracking.id, active=False)
        stopped = tracking_service.get_tracking(tracking.id)
        assert stopped.is_active is False
        # استئناف: إعادة تسليح وتصفير
        tracking_service.set_tracking_active(tracking.id, active=True)
        resumed = tracking_service.get_tracking(tracking.id)
        assert resumed.alert_armed is True
        assert resumed.last_alerted_price is None


class TestAlertEdit:
    def test_update_alert_resets_state(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)
        alert_service.evaluate_and_claim(tracking, 3900)

        # تعديل النطاق دون حذف المنتج
        updated = tracking_service.update_tracking_alert(
            tracking.id, alert_type="price_range",
            target_min_price=3000, target_max_price=3200,
        )
        assert updated is not None
        assert updated.target_max_price == 3200
        assert updated.alert_armed is True
        assert updated.last_alerted_price is None
        assert updated.last_alerted_at is None
        # نفس المنتج — لم يُنشأ منتج جديد
        assert updated.product_id == tracking.product_id
        assert updated.id == tracking.id

    def test_switch_alert_type(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        updated = tracking_service.update_tracking_alert(
            tracking.id, alert_type="max_price", target_price=4500,
        )
        assert updated.alert_type == "max_price"
        assert updated.target_price == 4500
        assert updated.target_min_price is None
        assert updated.target_max_price is None

        # الشرط الجديد: 4600 لا إشعار، 4400 إشعار
        assert alert_service.evaluate_and_claim(updated, 4600)[1] is False
        assert alert_service.evaluate_and_claim(updated, 4400)[1] is True

    def test_edit_alert_with_current_price_met_sends_alert(self, sample_user):
        """بعد التعديل إذا السعر الحالي يحقق الشرط الجديد → إشعار فوري."""
        product, _ = _make_product(price=3100)
        tracking = _make_range_tracking(sample_user, product, min_price=3000, max_price=3200)
        tracking_service.clear_alert_state(tracking.id)

        refreshed = tracking_service.get_tracking(tracking.id)
        decision, message = alert_service.evaluate_initial_alert(refreshed, product, product.current_price)
        assert message != ""
        assert tracking_service.get_tracking(tracking.id).last_alerted_price == 3100


class TestAtomicClaim:
    def test_double_claim_only_one_wins(self, sample_user):
        """محاكاة دورتَي فحص متوازيتين: حجز واحد فقط ينجح — لا إشعارات مكررة."""
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)

        from app.services.tracking_service import claim_alert
        from app.services.alert_evaluator import evaluate_alert

        fresh = tracking_service.get_tracking(tracking.id)
        decision = evaluate_alert(fresh, 3900)
        assert decision.alert_kind == "new"
        first = claim_alert(tracking.id, decision, 3900, expected_armed=True, expected_last_price=None)
        second = claim_alert(tracking.id, decision, 3900, expected_armed=True, expected_last_price=None)
        assert (first, second) == (True, False)

    def test_improved_claim_protects_last_price(self, sample_user):
        """إشعار التحسن: الحجز يشترط أن last_alerted_price لم يتغير (حماية سباق)."""
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)
        tracking_service.update_tracking_alert(
            tracking.id, alert_type="price_range",
            target_min_price=3500, target_max_price=4000,
            notify_on_additional_drop=True, additional_drop_percentage=5,
        )
        fresh = tracking_service.get_tracking(tracking.id)
        # تنبيه أول عند 3900
        assert alert_service.evaluate_and_claim(fresh, 3900)[1] is True

        state = tracking_service.get_tracking(tracking.id)
        from app.services.tracking_service import claim_alert
        from app.services.alert_evaluator import evaluate_alert

        decision = evaluate_alert(state, 3700)  # انخفاض 5.1% → improved
        assert decision.alert_kind == "improved"

        # سباق: آخر سعر تغيّر بين القراءة والحجز → الحجز القديم يفشل بعد أول حجز
        first = claim_alert(tracking.id, decision, 3700, expected_armed=False, expected_last_price=state.last_alerted_price)
        stale = claim_alert(tracking.id, decision, 3700, expected_armed=False, expected_last_price=state.last_alerted_price)
        assert first is True
        assert stale is False


class TestOwnershipProtection:
    def test_user_cannot_access_others_tracking(self, sample_user):
        product, _ = _make_product()
        tracking = _make_range_tracking(sample_user, product)
        intruder = user_service.get_or_create_user(999999999002, "Intruder", "X")[0]
        assert tracking_service.get_user_tracking_by_id(tracking.id, intruder.telegram_id) is None
        assert tracking_service.get_user_tracking_by_id(tracking.id, sample_user.telegram_id) is not None

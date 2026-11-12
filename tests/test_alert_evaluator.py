"""اختبارات منطق التنبيه النقي — الأنواع الثلاثة وجدول سلوك النطاق السعري كاملًا.

السيناريو المرجعي (من المواصفات):
النطاق: الحد الأدنى 3500، الحد الأعلى 4000.
- 4000 / 3900 / 3500 / 3000 → إشعار.
- 4001 / 4100 / 5000 → لا إشعار.
- لا تكرار ما دام الشرط متحققًا، وإعادة تسليح عند الارتفاع، وإشعار جديد عند الانخفاض مجددًا.
"""
from __future__ import annotations

from app.services.alert_evaluator import AlertType, evaluate_alert


class TestPriceRangeBehavior:
    """جدول سلوك النطاق السعري 3500 - 4000."""

    def _tracking(self, fake_tracking, armed=True):
        return fake_tracking(
            alert_type=AlertType.PRICE_RANGE.value,
            target_min_price=3500,
            target_max_price=4000,
            alert_armed=armed,
        )

    # ---------- أول فحص (التنبيه مسلح) ----------

    def test_5000_no_alert(self, fake_tracking):
        decision = evaluate_alert(self._tracking(fake_tracking), 5000)
        assert decision.condition_met is False
        assert decision.should_alert is False

    def test_4500_no_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 4500).should_alert is False

    def test_4001_no_alert(self, fake_tracking):
        """السعر 4001 — أعلى من الحد الأعلى بجنيه واحد: لا إشعار."""
        assert evaluate_alert(self._tracking(fake_tracking), 4001).should_alert is False

    def test_4000_alert(self, fake_tracking):
        """السعر 4000 = الحد الأعلى بالضبط: إشعار."""
        decision = evaluate_alert(self._tracking(fake_tracking), 4000)
        assert decision.condition_met is True
        assert decision.should_alert is True
        assert decision.alert_kind == "new"

    def test_3900_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 3900).should_alert is True

    def test_3500_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 3500).should_alert is True

    def test_3000_below_min_alert(self, fake_tracking):
        """أهم حالة: سعر أقل من الحد الأدنى → إشعار أيضًا (فرصة شراء أفضل)."""
        decision = evaluate_alert(self._tracking(fake_tracking), 3000)
        assert decision.condition_met is True
        assert decision.should_alert is True
        assert decision.price_status.value == "below_range"

    def test_2500_below_min_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 2500).should_alert is True

    # ---------- حالة السعر (inside / below / above) ----------

    def test_price_status_above(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 4100).price_status.value == "above_range"

    def test_price_status_inside(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 3700).price_status.value == "inside_range"

    def test_price_status_below(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 3200).price_status.value == "below_range"

    # ---------- منع التكرار (التنبيه غير مسلح) ----------

    def test_no_repeat_while_condition_met(self, fake_tracking):
        """تنبيه عند 4000 ثم 3900 ثم 3500 ثم 3000 — لا تكرار ما دام الشرط متحققًا."""
        tracking = self._tracking(fake_tracking, armed=False)
        tracking.last_alerted_price = 4000
        assert evaluate_alert(tracking, 3900).should_alert is False
        assert evaluate_alert(tracking, 3500).should_alert is False
        assert evaluate_alert(tracking, 3000).should_alert is False

    # ---------- إعادة التسليح والإشعار الجديد ----------

    def test_rearm_on_price_increase(self, fake_tracking):
        """ارتفع السعر إلى 4100 → إعادة تسليح بدون إشعار."""
        tracking = self._tracking(fake_tracking, armed=False)
        tracking.last_alerted_price = 3900
        decision = evaluate_alert(tracking, 4100)
        assert decision.condition_met is False
        assert decision.should_alert is False
        assert decision.rearm is True

    def test_no_double_rearm(self, fake_tracking):
        tracking = self._tracking(fake_tracking, armed=True)
        assert evaluate_alert(tracking, 4100).rearm is False

    def test_new_alert_after_rearm(self, fake_tracking):
        """بعد إعادة التسليح عند 4100 ثم انخفض إلى 3950 → إشعار جديد."""
        tracking = self._tracking(fake_tracking, armed=True)  # أعيد تسليحه
        decision = evaluate_alert(tracking, 3950)
        assert decision.should_alert is True
        assert decision.alert_kind == "new"

    # ---------- الحالة المكتملة كاملة (كما في المواصفات) ----------

    def test_full_lifecycle(self, fake_tracking):
        """دورة حياة كاملة: تنبيه → لا تكرار → ارتفاع → تسليح → انخفاض → تنبيه جديد."""
        armed = True
        # 5000: لا إشعار، تنبيه مسلح
        d = evaluate_alert(self._tracking(fake_tracking, armed), 5000)
        assert d.should_alert is False and d.rearm is False

        # 4000: إشعار → غير مسلح
        d = evaluate_alert(self._tracking(fake_tracking, armed), 4000)
        assert d.should_alert is True
        armed = False

        # 3900: لا تكرار
        d = evaluate_alert(self._tracking(fake_tracking, armed), 3900)
        assert d.should_alert is False and d.rearm is False

        # 4100: إعادة تسليح
        d = evaluate_alert(self._tracking(fake_tracking, armed), 4100)
        assert d.rearm is True
        armed = True

        # 3950: إشعار جديد
        d = evaluate_alert(self._tracking(fake_tracking, armed), 3950)
        assert d.should_alert is True


class TestMaxPriceAlert:
    """النوع الثاني: السعر الأقصى."""

    def _tracking(self, fake_tracking, armed=True):
        return fake_tracking(
            alert_type=AlertType.MAX_PRICE.value,
            target_price=4000,
            alert_armed=armed,
        )

    def test_4001_no_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 4001).should_alert is False

    def test_4000_alert(self, fake_tracking):
        decision = evaluate_alert(self._tracking(fake_tracking), 4000)
        assert decision.should_alert is True and decision.alert_kind == "new"

    def test_3900_alert_then_no_repeat(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 3900).should_alert is True
        tracking = self._tracking(fake_tracking, armed=False)
        tracking.last_alerted_price = 3900
        assert evaluate_alert(tracking, 3900).should_alert is False

    def test_4100_rearm_then_3950_new_alert(self, fake_tracking):
        tracking = self._tracking(fake_tracking, armed=False)
        assert evaluate_alert(tracking, 4100).rearm is True
        tracking2 = self._tracking(fake_tracking, armed=True)
        assert evaluate_alert(tracking2, 3950).alert_kind == "new"


class TestPercentageDropAlert:
    """النوع الثالث: نسبة الانخفاض — مع السعر المرجعي وطريقة الحساب."""

    def _tracking(self, fake_tracking, armed=True, reference=5000, percentage=10):
        return fake_tracking(
            alert_type=AlertType.PERCENTAGE_DROP.value,
            target_drop_percentage=percentage,
            reference_price=reference,
            alert_armed=armed,
        )

    def test_threshold_calculation(self, fake_tracking):
        """مرجعي 5000 ونسبة 10% → العتبة 4500."""
        decision = evaluate_alert(self._tracking(fake_tracking), 5000)
        assert decision.threshold_price == 4500

    def test_4501_no_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 4501).should_alert is False

    def test_4500_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 4500).should_alert is True

    def test_4000_alert(self, fake_tracking):
        assert evaluate_alert(self._tracking(fake_tracking), 4000).should_alert is True

    def test_no_repeat_below_threshold(self, fake_tracking):
        tracking = self._tracking(fake_tracking, armed=False)
        tracking.last_alerted_price = 4500
        assert evaluate_alert(tracking, 4000).should_alert is False

    def test_drop_percentage_reported(self, fake_tracking):
        """نسبة الانخفاض الفعلية مقابل المرجعي تُبلغ بدقة."""
        decision = evaluate_alert(self._tracking(fake_tracking), 4500)
        assert decision.drop_percentage == 10.0

    def test_reference_change_recomputes_threshold(self, fake_tracking):
        """تغيير السعر المرجعي إلى 6000 → العتبة 5400 → سعر 5000 يحقق الشرط."""
        tracking = self._tracking(fake_tracking, reference=6000)
        decision = evaluate_alert(tracking, 5000)
        assert decision.threshold_price == 5400
        assert decision.should_alert is True

    def test_missing_reference_never_alerts(self, fake_tracking):
        tracking = self._tracking(fake_tracking)
        tracking.reference_price = None
        assert evaluate_alert(tracking, 100).should_alert is False


class TestAdditionalDropAlert:
    """تنبيهات التحسن الإضافي — إشعار جديد عند انخفاض إضافي عن آخر تنبيه."""

    def _tracking(self, fake_tracking, armed=False, last_alerted=3900, add_pct=5, notify=True):
        return fake_tracking(
            alert_type=AlertType.PRICE_RANGE.value,
            target_min_price=3500,
            target_max_price=4000,
            alert_armed=armed,
            notify_on_additional_drop=notify,
            additional_drop_percentage=add_pct,
            last_alerted_price=last_alerted,
        )

    def test_small_drop_no_alert(self, fake_tracking):
        """انخفاض 2.5% عن آخر تنبيه (3900 → 3800) — أقل من 5%: لا إشعار."""
        assert evaluate_alert(self._tracking(fake_tracking), 3800).should_alert is False

    def test_sufficient_drop_alerts(self, fake_tracking):
        """انخفاض أكثر من 5% عن آخر تنبيه (3900 → 3700): إشعار تحسن."""
        decision = evaluate_alert(self._tracking(fake_tracking), 3700)
        assert decision.should_alert is True
        assert decision.alert_kind == "improved"

    def test_disabled_no_alert(self, fake_tracking):
        tracking = self._tracking(fake_tracking)
        tracking.notify_on_additional_drop = False
        assert evaluate_alert(tracking, 3700).should_alert is False

    def test_armed_takes_priority_over_improved(self, fake_tracking):
        """إذا التنبيه مسلح → إشعار جديد (وليس تحسن)."""
        decision = evaluate_alert(self._tracking(fake_tracking, armed=True), 3700)
        assert decision.alert_kind == "new"


class TestEdgeCases:
    def test_range_without_max_never_alerts(self, fake_tracking):
        tracking = fake_tracking(alert_type=AlertType.PRICE_RANGE.value, target_min_price=3500)
        assert evaluate_alert(tracking, 100).should_alert is False

    def test_unknown_alert_type_never_alerts(self, fake_tracking):
        tracking = fake_tracking(alert_type="weird", target_max_price=4000)
        assert evaluate_alert(tracking, 100).should_alert is False

    def test_exact_equality_boundary(self, fake_tracking):
        """الحد الأعلى 4000.01 والسعر 4000.01 → إشعار (تساوي)."""
        tracking = fake_tracking(
            alert_type=AlertType.PRICE_RANGE.value,
            target_min_price=3500,
            target_max_price=4000.01,
        )
        assert evaluate_alert(tracking, 4000.01).should_alert is True

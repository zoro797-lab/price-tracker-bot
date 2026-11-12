"""منطق تقييم شروط التنبيه — منطق نقي خالص (بدون قاعدة بيانات أو Telegram).

هذا هو قلب نظام السعر المستهدف، ويغطي الأنواع الثلاثة:

1. نطاق سعري (price_range):
   - الإشعار يتحقق عندما يكون السعر الحالي أقل من أو يساوي الحد الأعلى.
   - الحد الأدنى إرشادي فقط: أي سعر أقل من الحد الأدنى يُرسل إشعارًا أيضًا
     (لأنه فرصة شراء أفضل).
   - مثال: النطاق 3500 - 4000 → إشعار عند 4000 / 3900 / 3500 / 3000.
     لا إشعار عند 4001.

2. سعر أقصى (max_price): إشعار عندما السعر <= السعر الأقصى المحدد.

3. نسبة انخفاض (percentage_drop): إشعار عندما
   السعر <= السعر المرجعي × (1 - النسبة/100).

قواعد منع التكرار (Alert State):
- alert_armed=True  → الشرط لم يتحقق بعد (أو أعيد تسليحه بعد ارتفاع السعر).
- عند أول تحقق للشرط و alert_armed=True → إرسال إشعار ثم alert_armed=False.
- طالما الشرط متحقق و alert_armed=False → لا تكرار (إلا مع تنبيه التحسن الإضافي).
- ارتفع السعر فوق الحد → إعادة تسليح (alert_armed=True).
- انخفض بعدها إلى الحد أو أقل → إشعار جديد.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

EPSILON = 1e-9


class AlertType(str, Enum):
    PRICE_RANGE = "price_range"
    MAX_PRICE = "max_price"
    PERCENTAGE_DROP = "percentage_drop"


class PriceStatus(str, Enum):
    ABOVE_RANGE = "above_range"    # أعلى من الحد الأعلى / فوق المستهدف
    INSIDE_RANGE = "inside_range"  # داخل النطاق المحدد
    BELOW_RANGE = "below_range"    # أقل من الحد الأدنى (فرصة أفضل)
    TARGET_MET = "target_met"      # حقق الشرط (للأنواع الأخرى)


@dataclass(frozen=True)
class AlertDecision:
    """نتيجة تقييم الشرط لمتابعة واحدة عند سعر معين."""

    condition_met: bool           # هل السعر يحقق شرط التنبيه؟
    should_alert: bool            # هل يجب إرسال إشعار الآن؟
    alert_kind: str | None        # "new" (إشعار جديد) | "improved" (تحسن إضافي) | None
    rearm: bool                   # هل يجب إعادة تسليح التنبيه؟
    price_status: PriceStatus     # وصف موقع السعر
    threshold_price: float | None  # السعر الذي يفعّل التنبيه
    drop_percentage: float | None  # نسبة الانخفاض الفعلية مقارنة بالمرجع (إن وجد)


def _drop_percentage(reference: float | None, price: float) -> float | None:
    """نسبة انخفاض السعر مقارنة بسعر مرجعي (موجبة إذا انخفض)."""
    if reference is None or reference <= 0:
        return None
    return max(0.0, round((reference - price) / reference * 100, 2))


def evaluate_alert(tracking, price: float) -> AlertDecision:
    """تقييم شرط التنبيه لمتابعة (UserTracking) عند سعر جديد.

    `tracking` يحتاج الحقول: alert_type, target_min_price, target_max_price,
    target_price, target_drop_percentage, reference_price,
    alert_armed, notify_on_additional_drop, additional_drop_percentage,
    last_alerted_price
    """
    alert_type = tracking.alert_type

    # ---------- تحديد العتبة وحالة السعر حسب نوع التنبيه ----------
    if alert_type == AlertType.PRICE_RANGE.value:
        threshold = tracking.target_max_price
        if threshold is None:
            return AlertDecision(False, False, None, False, PriceStatus.ABOVE_RANGE, None, None)
        condition_met = price <= threshold + EPSILON
        minimum = tracking.target_min_price
        if condition_met:
            status = (
                PriceStatus.BELOW_RANGE
                if minimum is not None and price < minimum - EPSILON
                else PriceStatus.INSIDE_RANGE
            )
        else:
            status = PriceStatus.ABOVE_RANGE
        drop_pct = _drop_percentage(tracking.reference_price, price)

    elif alert_type == AlertType.MAX_PRICE.value:
        threshold = tracking.target_price
        if threshold is None:
            return AlertDecision(False, False, None, False, PriceStatus.ABOVE_RANGE, None, None)
        condition_met = price <= threshold + EPSILON
        status = PriceStatus.TARGET_MET if condition_met else PriceStatus.ABOVE_RANGE
        drop_pct = _drop_percentage(tracking.reference_price, price)

    elif alert_type == AlertType.PERCENTAGE_DROP.value:
        reference = tracking.reference_price
        percentage = tracking.target_drop_percentage
        if reference is None or percentage is None or reference <= 0:
            return AlertDecision(False, False, None, False, PriceStatus.ABOVE_RANGE, None, None)
        threshold = round(reference * (1 - percentage / 100), 2)
        condition_met = price <= threshold + EPSILON
        status = PriceStatus.TARGET_MET if condition_met else PriceStatus.ABOVE_RANGE
        drop_pct = _drop_percentage(reference, price)

    else:
        return AlertDecision(False, False, None, False, PriceStatus.ABOVE_RANGE, None, None)

    # ---------- قواعد الحالة ومنع التكرار ----------
    if not condition_met:
        # السعر فوق الحد: أعد تسليح التنبيه (بدون إشعار) حتى ينخفض مرة أخرى
        rearm = not tracking.alert_armed
        return AlertDecision(False, False, None, rearm, status, threshold, drop_pct)

    if tracking.alert_armed:
        # أول تحقق للشرط (أو إشعار جديد بعد إعادة التسليح)
        return AlertDecision(True, True, "new", False, status, threshold, drop_pct)

    # الشرط متحقق لكن التنبيه غير مسلح → لا تكرار
    # إلا إذا فعّل المستخدم تنبيهات التحسن الإضافي ووجدنا انخفاضًا إضافيًا كافيًا
    if tracking.notify_on_additional_drop and tracking.additional_drop_percentage:
        base_price = tracking.last_alerted_price
        if base_price and base_price > 0:
            improved_threshold = base_price * (1 - tracking.additional_drop_percentage / 100)
            if price <= improved_threshold + EPSILON:
                return AlertDecision(True, True, "improved", False, status, threshold, drop_pct)

    return AlertDecision(True, False, None, False, status, threshold, drop_pct)


def price_status_text(status: PriceStatus, alert_type: str) -> str:
    """وصف عربي لحالة السعر."""
    if alert_type == AlertType.PRICE_RANGE.value:
        return {
            PriceStatus.ABOVE_RANGE: "أعلى من النطاق المحدد",
            PriceStatus.INSIDE_RANGE: "داخل النطاق المحدد ✅",
            PriceStatus.BELOW_RANGE: "أقل من النطاق المحدد — فرصة شراء أفضل! 🎉",
        }.get(status, "—")
    return {
        PriceStatus.TARGET_MET: "حقق السعر المستهدف ✅",
        PriceStatus.ABOVE_RANGE: "أعلى من السعر المستهدف",
    }.get(status, "—")

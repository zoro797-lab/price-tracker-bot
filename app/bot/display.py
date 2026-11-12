"""بناء نصوص بطاقة المنتج — تُستخدم في القوائم والتفاصيل ورسائل التنبيه."""
from __future__ import annotations

from app.database import UserTracking
from app.services.alert_service import ALERT_TYPE_NAMES
from app.services.price_history_service import get_stats, get_recent_readings
from app.utils.formatting import format_datetime, format_price, format_percentage, short_text


def store_display_name(store_key: str) -> str:
    from app.providers.registry import PROVIDERS_BY_KEY
    provider = PROVIDERS_BY_KEY.get(store_key)
    return provider.store_display_name if provider else store_key


def availability_text(status: str) -> str:
    return {
        "available": "✅ متاح",
        "unavailable": "❌ غير متاح",
        "unknown": "❔ غير معروف",
    }.get(status, status)


def alert_status_text(tracking: UserTracking) -> str:
    """حالة التنبيه — مسلح بانتظار تحقق الشرط، أو سبق التنبيه."""
    if not tracking.is_active:
        return "⏸️ المتابعة موقوفة"
    if tracking.alert_armed:
        return "🟢 مسلح — بانتظار وصول السعر للمستوى المطلوب"
    return "🔔 تم التنبيه — سيعاد التسليح تلقائيًا إذا ارتفع السعر فوق الحد"


def product_card_text(tracking: UserTracking) -> str:
    """بطاقة المنتج الكاملة (نقطة 8 من المتطلبات):
    السعر الحالي + نوع التنبيه + الحدود إن كان نطاقًا + حالة التنبيه + آخر إشعار."""
    product = tracking.product
    lines = [
        f"📦 <b>{short_text(product.title, 70)}</b>",
        f"🏪 المتجر: {store_display_name(product.store)}",
        f"💰 السعر الحالي: {format_price(product.current_price)}",
        f"🛒 التوفر: {availability_text(product.availability_status)}",
        "",
        f"🎯 نوع التنبيه: {ALERT_TYPE_NAMES.get(tracking.alert_type, tracking.alert_type)}",
    ]

    if tracking.alert_type == "price_range":
        lines.append(f"📉 الحد الأدنى: {format_price(tracking.target_min_price)}")
        lines.append(f"📈 الحد الأعلى: {format_price(tracking.target_max_price)}")
        lines.append("🔔 الإشعار عند: السعر ≤ الحد الأعلى (حتى لو انخفض تحت الحد الأدنى)")
    elif tracking.alert_type == "max_price":
        lines.append(f"🔝 السعر الأقصى: {format_price(tracking.target_price)}")
        lines.append("🔔 الإشعار عند: السعر ≤ السعر الأقصى")
    elif tracking.alert_type == "percentage_drop":
        lines.append(f"📉 نسبة الانخفاض المطلوبة: {format_percentage(tracking.target_drop_percentage)}")
        lines.append(f"📌 السعر المرجعي: {format_price(tracking.reference_price)}")
        lines.append("🔔 الإشعار عند انخفاض السعر عن المرجعي بالنسبة المطلوبة")

    if tracking.notify_on_additional_drop and tracking.additional_drop_percentage:
        lines.append(f"➕ تنبيه التحسن الإضافي: عند انخفاض إضافي {format_percentage(tracking.additional_drop_percentage)}")

    lines += [
        "",
        f"⚡ حالة التنبيه: {alert_status_text(tracking)}",
    ]

    if tracking.last_alerted_price is not None:
        lines.append(f"🔔 آخر إشعار: {format_price(tracking.last_alerted_price)} بتاريخ {format_datetime(tracking.last_alerted_at)}")
    else:
        lines.append("🔔 آخر إشعار: لم يُرسل بعد")

    lines += [
        "",
        f"📅 آخر تحديث للسعر: {format_datetime(product.last_checked_at)}",
        f"📅 تاريخ الإضافة: {format_datetime(tracking.created_at)}",
    ]
    return "\n".join(lines)


def product_detail_text(tracking: UserTracking) -> str:
    """تفاصيل موسعة + إحصائيات التاريخ المتاح فعليًا."""
    text = product_card_text(tracking)
    stats = get_stats(tracking.product_id, days=30)
    lines = ["", "📊 <b>إحصائيات آخر 30 يومًا (المتاح فعليًا):</b>"]
    if stats.readings_count == 0:
        lines.append("لا توجد قراءات بعد — ستظهر الإحصائيات بعد أول فحص.")
    else:
        lines.append(f"• عدد أيام القراءات: {stats.days_count} يومًا ({stats.readings_count} قراءة)")
        lines.append(f"• أقل سعر: {format_price(stats.min_price)}")
        lines.append(f"• أعلى سعر: {format_price(stats.max_price)}")
        lines.append(f"• متوسط السعر: {format_price(stats.avg_price)}")
        if stats.change_percentage is not None:
            direction = "📈 ارتفاع" if stats.change_percentage > 0 else "📉 انخفاض" if stats.change_percentage < 0 else "➖ ثبات"
            lines.append(f"• نسبة التغير: {direction} {format_percentage(abs(stats.change_percentage))}")
    return text + "\n" + "\n".join(lines)


def history_text(tracking: UserTracking) -> str:
    """نص سجل الأسعار — بقراءات حقيقية فقط وتسمية صادقة للفترة."""
    product = tracking.product
    stats = get_stats(tracking.product_id, days=30)
    header = [
        f"📊 <b>سجل الأسعار</b>",
        f"📦 {short_text(product.title, 60)}",
        "",
    ]
    if stats.readings_count == 0:
        header.append("📭 لا توجد قراءات مسجلة بعد. سيتم تسجيل الأسعار عند كل فحص ناجح.")
        return "\n".join(header)

    header.append(f"⏱️ الفترة المتاحة فعليًا: {stats.days_count} يومًا (منذ {format_datetime(stats.first_recorded_at)})")
    header.append(f"🔢 عدد القراءات: {stats.readings_count}")
    header.append(f"📉 أقل سعر: {format_price(stats.min_price)}")
    header.append(f"📈 أعلى سعر: {format_price(stats.max_price)}")
    header.append(f"⚖️ متوسط السعر: {format_price(stats.avg_price)}")
    if stats.change_percentage is not None:
        header.append(f"🔁 نسبة التغير خلال الفترة: {format_percentage(stats.change_percentage)}")

    readings = get_recent_readings(tracking.product_id, limit=10)
    header += ["", "🕐 <b>آخر القراءات:</b>"]
    for reading in reversed(readings):
        header.append(f"• {format_datetime(reading.recorded_at)} — {format_price(reading.price)}")
    return "\n".join(header)

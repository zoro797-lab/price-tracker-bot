"""خدمة التنبيهات: بناء الرسائل + التقييم والحجز الذري + الإرسال.

التقسيم:
- الدوال المتزامنة (evaluate_and_claim, build_alert_message...) قابلة للاختبار
  بدون asyncio وبدون Telegram.
- دالة process_tracking هي التنسيق النهائي (async) المستخدمة في الفحص المجدول.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database import Product, UserTracking
from app.providers.base import BaseProvider
from app.services import price_history_service as history_service
from app.services import tracking_service
from app.services.alert_evaluator import AlertDecision, AlertType, evaluate_alert, price_status_text
from app.services.notification_service import mark_failed, mark_sent, queue_notification
from app.utils.formatting import format_datetime, format_price, format_percentage, short_text
from app.utils.logger import get_logger

logger = get_logger("alerts")

ALERT_TYPE_NAMES = {
    AlertType.PRICE_RANGE.value: "نطاق سعري 📊",
    AlertType.MAX_PRICE.value: "سعر أقصى 🔝",
    AlertType.PERCENTAGE_DROP.value: "نسبة انخفاض 📉",
}


def alert_target_description(tracking: UserTracking) -> str:
    """وصف عربي لإعداد التنبيه (يُستخدم في البطاقات ورسائل الأدمن)."""
    if tracking.alert_type == AlertType.PRICE_RANGE.value:
        return (
            f"نطاق سعري: من {format_price(tracking.target_min_price)} "
            f"إلى {format_price(tracking.target_max_price)}\n"
            f"سيتم الإشعار عندما يصبح السعر {format_price(tracking.target_max_price)} أو أقل"
        )
    if tracking.alert_type == AlertType.MAX_PRICE.value:
        return (
            f"سعر أقصى: {format_price(tracking.target_price)}\n"
            f"سيتم الإشعار عندما يصبح السعر {format_price(tracking.target_price)} أو أقل"
        )
    if tracking.alert_type == AlertType.PERCENTAGE_DROP.value:
        threshold = (
            round(tracking.reference_price * (1 - tracking.target_drop_percentage / 100), 2)
            if tracking.reference_price and tracking.target_drop_percentage is not None
            else None
        )
        return (
            f"نسبة انخفاض: {format_percentage(tracking.target_drop_percentage)}\n"
            f"السعر المرجعي: {format_price(tracking.reference_price)}\n"
            f"طريقة الحساب: {format_price(tracking.reference_price)} × (1 - "
            f"{format_percentage(tracking.target_drop_percentage)}) = {format_price(threshold)}\n"
            f"سيتم الإشعار عندما يصبح السعر {format_price(threshold)} أو أقل"
        )
    return "غير معروف"


def build_alert_message(tracking: UserTracking, product: Product, price: float,
                        decision: AlertDecision, kind: str) -> str:
    """بناء رسالة التنبيه — لا تُظهر إحصائيات 30 يومًا إلا إذا تاريخ حقيقي متوفر."""
    store_name = _store_display_name(product.store)
    emoji = "🎉 خبر سار! السعر وصل للمستوى المطلوب."
    if kind == "improved":
        emoji = "📉 انخفاض إضافي! السعر أصبح أفضل من آخر تنبيه."

    lines = [
        emoji,
        "",
        f"📦 اسم المنتج: {short_text(product.title, 80)}",
        f"🏪 المتجر: {store_name}",
        f"💰 السعر الحالي: {format_price(price)}",
    ]

    lines.append(f"🎯 نوع التنبيه: {ALERT_TYPE_NAMES.get(tracking.alert_type, tracking.alert_type)}")

    if tracking.alert_type == AlertType.PRICE_RANGE.value:
        lines.append(f"🎯 نطاقك المستهدف: {format_price(tracking.target_min_price)} - {format_price(tracking.target_max_price)}")
        lines.append(f"📍 حالة السعر: {price_status_text(decision.price_status, tracking.alert_type)}")
    elif tracking.alert_type == AlertType.MAX_PRICE.value:
        lines.append(f"🎯 سعرك المستهدف الأقصى: {format_price(tracking.target_price)}")
    elif tracking.alert_type == AlertType.PERCENTAGE_DROP.value:
        lines.append(f"🎯 نسبة الانخفاض المطلوبة: {format_percentage(tracking.target_drop_percentage)}")
        lines.append(f"📌 السعر المرجعي: {format_price(tracking.reference_price)}")
        lines.append(
            f"🧮 طريقة الحساب: {format_price(tracking.reference_price)} × (1 - "
            f"{format_percentage(tracking.target_drop_percentage)}) = {format_price(decision.threshold_price)}"
        )

    if decision.drop_percentage:
        lines.append(f"📉 السعر منخفض بنسبة {format_percentage(decision.drop_percentage)} مقارنة بالسعر المرجعي")

    # إحصائيات التاريخ — بشفافية كاملة
    stats = history_service.get_stats(product.id, days=30)
    if stats.days_count >= 2 and stats.min_price is not None:
        lines.append(f"📊 أقل سعر مسجل خلال {stats.days_count} يومًا (المتاح فعليًا): {format_price(stats.min_price)}")

    if tracking.last_alerted_price and kind == "improved":
        lines.append(f"⏮️ سعر آخر تنبيه: {format_price(tracking.last_alerted_price)}")

    lines += [
        "",
        f"🔗 رابط المنتج: {product.product_url}",
        f"📅 {format_datetime(format_datetime_now())}",
    ]
    return "\n".join(lines)


def format_datetime_now():
    from app.utils.formatting import now as _now
    return _now()


def build_alert_keyboard(tracking: UserTracking) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 فتح المنتج", url=tracking.product.product_url)],
        [
            InlineKeyboardButton(text="📊 سجل الأسعار", callback_data=f"history:{tracking.id}"),
            InlineKeyboardButton(text="📈 الرسم البياني", callback_data=f"chart:{tracking.id}:30"),
        ],
        [
            InlineKeyboardButton(text="✏️ تعديل التنبيه", callback_data=f"editalert:{tracking.id}"),
            InlineKeyboardButton(text="⏸️ إيقاف المتابعة", callback_data=f"stoptrack:{tracking.id}"),
        ],
    ])


def evaluate_and_claim(tracking: UserTracking, price: float) -> tuple[AlertDecision, bool]:
    """تقييم الشرط ثم محاولة الحجز الذري. يعيد (القرار، هل حجزنا حق الإشعار؟)."""
    decision = evaluate_alert(tracking, price)

    if decision.rearm and not decision.should_alert:
        tracking_service.apply_rearm_atomically(tracking.id)
        logger.debug("Tracking %s re-armed (price %.2f above threshold)", tracking.id, price)
        return decision, False

    if decision.should_alert:
        claimed = tracking_service.claim_alert(
            tracking.id,
            decision,
            price,
            expected_armed=bool(tracking.alert_armed),
            expected_last_price=tracking.last_alerted_price,
        )
        if claimed:
            logger.info("Alert claimed for tracking %s (kind=%s, price=%.2f)", tracking.id, decision.alert_kind, price)
        else:
            logger.debug("Alert claim lost for tracking %s (concurrent check)", tracking.id)
        return decision, claimed

    return decision, False


async def send_alert(bot: Bot, tracking: UserTracking, product: Product, price: float,
                     decision: AlertDecision, kind: str) -> None:
    """إنشاء الإشعار كـ pending ثم إرساله وتحديث حالته."""
    user = _get_user(tracking.user_id)
    if user is None or user.is_blocked:
        return

    message = build_alert_message(tracking, product, price, decision, kind)
    notification_id = queue_notification(
        user_id=user.id,
        product_id=product.id,
        tracking_id=tracking.id,
        notification_type="price_alert" if kind == "new" else "additional_drop_alert",
        price_at_notification=price,
        message=message,
    )
    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message,
            reply_markup=build_alert_keyboard(tracking),
        )
        mark_sent(notification_id)
    except Exception as exc:
        logger.error("Failed sending alert %s: %s", notification_id, exc)
        mark_failed(notification_id)


def evaluate_initial_alert(tracking: UserTracking, product: Product, price: float) -> tuple[AlertDecision, str]:
    """أول فحص عند إضافة المنتج — إشعار فوري إذا الشرط متحقق من البداية.

    يعيد (القرار، رسالة جاهزة للإرسال أو "")."""
    decision = evaluate_alert(tracking, price)
    if decision.should_alert:
        tracking_service.claim_alert(
            tracking.id, decision, price,
            expected_armed=True, expected_last_price=None,
        )
        message = build_alert_message(tracking, product, price, decision, "new")
        queue_notification(
            user_id=tracking.user_id,
            product_id=product.id,
            tracking_id=tracking.id,
            notification_type="price_alert",
            price_at_notification=price,
            message=message,
        )
        return decision, message
    return decision, ""


def build_admin_new_product_message(user, product: Product, tracking: UserTracking) -> str:
    """رسالة الأدمن عند إضافة منتج — تشمل نوع التنبيه والنطاق المحدد."""
    return "\n".join([
        "🔔 منتج جديد تمت إضافته للمتابعة",
        "",
        f"👤 المستخدم: {(user.first_name or '') + ' ' + (user.last_name or '')}".strip(),
        f"🆔 Telegram ID: {user.telegram_id}",
        f"📱 Username: @{user.username}" if user.username else "📱 Username: —",
        f"🏪 المتجر: {_store_display_name(product.store)}",
        f"📦 اسم المنتج: {short_text(product.title, 80)}",
        f"💰 السعر الحالي: {format_price(product.current_price)}",
        f"🎯 نوع التنبيه: {ALERT_TYPE_NAMES.get(tracking.alert_type, tracking.alert_type)}",
    ] + (
        [
            f"📉 الحد الأدنى: {format_price(tracking.target_min_price)}",
            f"📈 الحد الأعلى: {format_price(tracking.target_max_price)}",
        ] if tracking.alert_type == AlertType.PRICE_RANGE.value
        else [f"🎯 القيمة المستهدفة: {format_price(tracking.target_price)}"]
        if tracking.alert_type == AlertType.MAX_PRICE.value
        else [
            f"📉 نسبة الانخفاض: {format_percentage(tracking.target_drop_percentage)}",
            f"📌 السعر المرجعي: {format_price(tracking.reference_price)}",
        ]
    ) + [
        "",
        f"🔗 رابط المنتج: {product.product_url}",
        f"📅 تاريخ الإضافة: {format_datetime(product.created_at)}",
    ])


def _get_user(user_id: int):
    from app.database import User, db_session
    with db_session() as session:
        return session.query(User).filter(User.id == user_id).first()


def _store_display_name(store_key: str) -> str:
    from app.providers.registry import PROVIDERS_BY_KEY
    provider = PROVIDERS_BY_KEY.get(store_key)
    return provider.store_display_name if provider else store_key

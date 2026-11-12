"""خدمة الفحص المجدول — تُستدعى من APScheduler دوريًا.

- تفحص المنتجات ذات المتابعات النشطة فقط.
- تجمع المتابعات على المنتج نفسه لتجنب تكرار طلبات السعر.
- تسجل كل فحص (نجاح/فشل) في price_check_logs.
- تقيّم شروط التنبيه لكل مستخدم بشكل مستقل.
- فشل منتج لا يوقف الفحص الكلي.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from app.database import PriceCheckLog, UserTracking, db_session
from app.providers import BlockedByStore, ProviderError, SourceUnavailable
from app.providers.registry import get_provider
from app.services import alert_service, price_history_service, settings_service, tracking_service
from app.services.notification_service import send_pending_notifications
from app.utils.formatting import localize, now
from app.utils.logger import get_logger

logger = get_logger("price_check")

# عتبة إبلاغ الأدمن عن تكرار أخطاء الفحص (عدد الفشل المتتالي)
CONSECUTIVE_FAILURE_ADMIN_THRESHOLD = 3


def get_active_products_with_trackings() -> dict[int, list[UserTracking]]:
    """خريطة product_id → قائمة المتابعات النشطة (تجميع المستخدمين على نفس المنتج)."""
    from sqlalchemy.orm import joinedload

    with db_session() as session:
        trackings = (
            session.query(UserTracking)
            .options(joinedload(UserTracking.product))
            .filter(UserTracking.is_active.is_(True))
            .all()
        )
    grouped: dict[int, list[UserTracking]] = {}
    for tracking in trackings:
        grouped.setdefault(tracking.product_id, []).append(tracking)
    return grouped


def get_consecutive_failures(product_id: int) -> int:
    """عدد الفشل المتتالي في آخر الفحوصات."""
    with db_session() as session:
        logs = (
            session.query(PriceCheckLog)
            .filter(PriceCheckLog.product_id == product_id)
            .order_by(PriceCheckLog.checked_at.desc())
            .limit(CONSECUTIVE_FAILURE_ADMIN_THRESHOLD)
            .all()
        )
        for log in logs:
            if log.status != "failed":
                return 0
        return len(logs)


def log_check(product_id: int | None, status: str, error_message: str | None = None) -> None:
    with db_session() as session:
        session.add(
            PriceCheckLog(
                product_id=product_id,
                status=status,
                error_message=error_message,
                checked_at=localize(now()),
            )
        )


async def check_single_product(bot, product) -> float | None:
    """فحص منتج واحد: جلب السعر + تسجيل القراءة + تقييم كل المتابعات النشطة.

    يعيد السعر الجديد أو None عند الفشل. فشل واحد لا يوقف النظام.
    """
    provider = get_provider(product.store)
    if provider is None:
        log_check(product.id, "failed", f"لا يوجد موصل للمتجر {product.store}")
        return None

    if not settings_service.is_store_enabled(product.store):
        logger.info("Store %s disabled — skipping product %s", product.store, product.id)
        log_check(product.id, "skipped", f"متجر {provider.store_display_name} غير مفعل حاليًا")
        return None

    # جلب السعر في Thread منفصل حتى لا يجمّد حلقة الأحداث
    try:
        snapshot = await asyncio.to_thread(provider.fetch_product, product.product_url)
    except (SourceUnavailable, BlockedByStore, ProviderError) as exc:
        logger.warning("Price check failed for product %s: %s", product.id, exc)
        log_check(product.id, "failed", str(exc))
        failures = get_consecutive_failures(product.id)
        if failures >= CONSECUTIVE_FAILURE_ADMIN_THRESHOLD:
            await _notify_admins_source_issue(bot, product, provider, failures)
        return None
    except Exception as exc:  # أي خطأ غير متوقع لا يوقف البوت
        logger.error("Unexpected error checking product %s: %s", product.id, exc)
        log_check(product.id, "failed", f"خطأ غير متوقع: {exc}")
        return None

    price = snapshot.price
    if price is None:
        log_check(product.id, "failed", "قراءة بلا سعر — لن تُسجل كسعر")
        return None

    # تسجيل القراءة وتحديث المنتج
    price_history_service.record_price(
        product_id=product.id,
        price=price,
        currency=snapshot.currency,
        data_source=snapshot.data_source,
        availability_status=snapshot.availability.value,
    )
    from app.services.product_service import update_product_price
    update_product_price(product.id, price, snapshot.availability.value, snapshot.data_source)
    log_check(product.id, "success")

    # تقييم شروط التنبيه لكل متابعة نشطة بشكل مستقل
    trackings = get_active_products_with_trackings().get(product.id, [])
    fresh_product = _get_product_record(product.id)
    for tracking in trackings:
        try:
            await _process_tracking(bot, tracking, fresh_product, price)
        except Exception as exc:
            logger.error("Alert processing failed for tracking %s: %s", tracking.id, exc)

    return price


async def _process_tracking(bot, tracking, product, price: float) -> None:
    if not settings_service.get_setting("alerts_enabled"):
        return

    decision, claimed = alert_service.evaluate_and_claim(tracking, price)
    if claimed:
        user_info = _get_user_info(tracking.user_id)
        if user_info is None:
            return
        telegram_id, is_blocked = user_info
        if is_blocked:
            return
        kind = decision.alert_kind or "new"
        message = alert_service.build_alert_message(tracking, product, price, decision, kind)
        notification_id = alert_service.queue_notification(
            user_id=tracking.user_id,
            product_id=product.id,
            tracking_id=tracking.id,
            notification_type="price_alert" if kind == "new" else "additional_drop_alert",
            price_at_notification=price,
            message=message,
        )
        # الإرسال مباشرة مع تحديث حالة الإشعار (sent/failed)
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=alert_service.build_alert_keyboard(tracking),
            )
            alert_service.mark_sent(notification_id)
        except Exception as exc:
            logger.error("Sending alert %s failed: %s", notification_id, exc)
            alert_service.mark_failed(notification_id)


def _get_user_info(user_id: int) -> tuple[int, bool] | None:
    """(telegram_id, is_blocked) أو None إذا المستخدم غير موجود."""
    from app.database import User, db_session
    with db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        return (user.telegram_id, user.is_blocked) if user else None


def _get_product_record(product_id: int):
    from app.services.product_service import get_product
    return get_product(product_id)


async def _notify_admins_source_issue(bot, product, provider, failures: int) -> None:
    from app.config import settings
    if not settings_service.get_setting("admin_notifications_enabled"):
        return
    message = "\n".join([
        "⚠️ تكرار أخطاء الفحص",
        f"🏪 المتجر: {provider.store_display_name}",
        f"📦 المنتج: {product.title[:60]}",
        f"❌ عدد الفشل المتتالي: {failures}",
        f"🔗 {product.product_url}",
    ])
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
        except Exception as exc:
            logger.warning("Failed notifying admin %s: %s", admin_id, exc)


async def check_all_products(bot) -> dict:
    """دورة الفحص الكاملة. يعيد ملخصًا للنتائج."""
    started = localize(now())
    grouped = get_active_products_with_trackings()
    logger.info("Check cycle started: %s products, %s active trackings", len(grouped), sum(len(t) for t in grouped.values()))

    results = {"products": 0, "success": 0, "failed": 0, "alerts": 0}
    # ملاحظة: عدد الإشعارات يُحسب من سجل الإشعارات بعد الدورة
    alerts_before = _count_sent_today()

    for product_id in list(grouped.keys()):
        from app.services.product_service import get_product
        product = get_product(product_id)
        if product is None:
            continue
        results["products"] += 1
        price = await check_single_product(bot, product)
        if price is not None:
            results["success"] += 1
        else:
            results["failed"] += 1

    results["alerts"] = _count_sent_today() - alerts_before
    logger.info("Check cycle finished in %s: %s", localize(now()) - started, results)
    return results


def _count_sent_today() -> int:
    from app.database import Notification
    with db_session() as session:
        cutoff = localize(now()) - timedelta(hours=24)
        return (
            session.query(Notification)
            .filter(Notification.status == "sent", Notification.sent_at >= cutoff)
            .count()
        )


async def run_startup_tasks(bot) -> None:
    """مهام عند الإقلاع: إعادة إرسال الإشعارات المعلقة (ضمان عدم ضياعها)."""
    sent = await send_pending_notifications(bot)
    if sent:
        logger.info("Re-sent %s pending notifications after startup", sent)


async def run_maintenance(bot) -> None:
    """صيانة دورية: إعادة الإشعارات المعلقة + تنظيف السجل القديم."""
    await send_pending_notifications(bot)
    retention = settings_service.get_setting("price_history_retention_days")
    deleted = price_history_service.cleanup_old_history(retention)
    if deleted:
        logger.info("Cleaned %s old price history records", deleted)

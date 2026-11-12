"""إعداد المهام المجدولة (APScheduler)."""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services import settings_service
from app.utils.logger import get_logger

logger = get_logger("scheduler")


def create_scheduler(bot) -> AsyncIOScheduler:
    """إنشاء المجدول:
    - فحص الأسعار كل (check_interval_hours) ساعة — قابل للتغيير من لوحة الأدمن.
    - صيانة دورية (إعادة الإشعارات المعلقة + تنظيف السجل) كل 30 دقيقة.
    """
    scheduler = AsyncIOScheduler(timezone="Africa/Cairo")

    async def price_check_job():
        from app.services.price_check_service import check_all_products
        try:
            await check_all_products(bot)
        except Exception as exc:
            logger.exception("Price check cycle crashed: %s", exc)

    async def maintenance_job():
        from app.services.price_check_service import run_maintenance
        try:
            await run_maintenance(bot)
        except Exception as exc:
            logger.exception("Maintenance job crashed: %s", exc)

    interval_hours = settings_service.get_setting("check_interval_hours")
    scheduler.add_job(
        price_check_job,
        IntervalTrigger(hours=interval_hours),
        id="price_check",
        replace_existing=True,
        max_instances=1,  # منع تشغيل دورتي فحص في نفس الوقت
        coalesce=True,
    )
    scheduler.add_job(
        maintenance_job,
        IntervalTrigger(minutes=30),
        id="maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduler created: price check every %s hours", interval_hours)
    return scheduler


def reschedule_price_check(scheduler: AsyncIOScheduler) -> None:
    """إعادة جدولة الفحص بعد تغيير الإعداد من لوحة الأدمن."""
    interval_hours = settings_service.get_setting("check_interval_hours")
    scheduler.reschedule_job("price_check", trigger=IntervalTrigger(hours=interval_hours))
    logger.info("Price check rescheduled to every %s hours", interval_hours)

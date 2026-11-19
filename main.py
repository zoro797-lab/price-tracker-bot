"""نقطة تشغيل البوت: تهيئة قاعدة البيانات، البوت، المعالجات، والمجدول.

وضعا التشغيل:
- Polling (افتراضي): للتشغيل المحلي على Windows — run.bat
- Webhook: للنشر على Render أو أي استضافة — يُفعل تلقائيًا داخل Render
  (وجود RENDER_EXTERNAL_URL) أو بوضع USE_WEBHOOK=true في .env
"""
from __future__ import annotations

import asyncio
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.bot import handlers_add, handlers_admin, handlers_charts, handlers_user
from app.bot.middlewares import UserContextMiddleware
from app.config import settings
from app.database.database import init_db
from app.scheduler.jobs import create_scheduler
from app.utils.logger import get_logger, setup_logging

logger = get_logger("main")

WEBHOOK_PATH = "/webhook"


def build_bot_commands() -> list[BotCommand]:
    """أوامر البوت — تظهر في زر ☰ Menu أسفل الشاشة وقائمة الأوامر."""
    return [
        BotCommand(command="start", description="🏠 القائمة الرئيسية"),
        BotCommand(command="add", description="➕ إضافة منتج جديد"),
        BotCommand(command="my", description="📦 منتجاتي المتابعة"),
        BotCommand(command="help", description="ℹ️ المساعدة والشرح"),
    ]


async def register_bot_commands(bot: Bot) -> None:
    """تسجيل أوامر البوت وتفعيل زر ☰ Menu الدائم."""
    await bot.set_my_commands(build_bot_commands())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Bot commands registered (%s) — persistent Menu button enabled", len(build_bot_commands()))


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()

    # تسجيل المستخدم تلقائيًا ومنع المحظورين — قبل أي معالج
    dispatcher.update.outer_middleware(UserContextMiddleware())

    # ترتيب الراوترات: الأدمن أولًا (لتقليل تصادم الأنماط) ثم المستخدم
    dispatcher.include_router(handlers_admin.router)
    dispatcher.include_router(handlers_add.router)
    dispatcher.include_router(handlers_charts.router)
    dispatcher.include_router(handlers_user.router)
    return dispatcher


# --------------------------------------------------------------------------
# وضع Polling — التشغيل المحلي
# --------------------------------------------------------------------------

async def run_polling(bot: Bot, dispatcher: Dispatcher, scheduler) -> None:
    from app.services.price_check_service import run_startup_tasks

    await register_bot_commands(bot)
    scheduler.start()
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await run_startup_tasks(bot)
        logger.info("Bot is polling — press Ctrl+C to stop")
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


# --------------------------------------------------------------------------
# وضع Webhook — النشر على Render أو أي استضافة HTTPS
# --------------------------------------------------------------------------

def build_webhook_app(bot: Bot, dispatcher: Dispatcher) -> web.Application:
    """تطبيق HTTP يحتوي:
    - / و /healthz: فحص صحة (لـ Render Health Check و UptimeRobot).
    - /webhook: استقبال تحديثات Telegram (محمي بسِر webhook).
    """
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "bot": "price-tracker"})

    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=settings.effective_webhook_secret,
    )
    app.router.add_post(WEBHOOK_PATH, webhook_handler.handle)
    setup_application(app, dispatcher, bot=bot)
    return app


async def run_webhook(bot: Bot, dispatcher: Dispatcher, scheduler) -> None:
    from app.services.price_check_service import run_startup_tasks

    # تسجيل الرابط عند Telegram
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.effective_webhook_secret,
        drop_pending_updates=False,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )
    logger.info("Webhook registered at %s", settings.webhook_url)

    # تشغيل خادم HTTP
    app = build_webhook_app(bot, dispatcher)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("HTTP server listening on port %s", port)

    await register_bot_commands(bot)
    scheduler.start()
    await run_startup_tasks(bot)
    logger.info("Bot is running in webhook mode on %s", settings.external_url)

    # إبقاء العملية حية
    try:
        await asyncio.Event().wait()  # تنتظر للأبد
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await bot.session.close()


# --------------------------------------------------------------------------
# نقطة الدخول
# --------------------------------------------------------------------------

async def main() -> None:
    setup_logging()
    logger.info("Starting Price Tracker Bot...")

    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN غير موجود. أنشئ ملف .env انسخ .env.example وضعه فيه.\n"
            "احصل على التوكن من @BotFather في Telegram."
        )
    if not settings.admin_id_list:
        logger.warning("ADMIN_IDS فارغ — لن تعمل لوحة الأدمن ولا الإشعارات الإدارية")

    # قاعدة البيانات + Migration الآمن (يحافظ على كل المتابعات القديمة)
    init_db(settings.database_path)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = create_dispatcher()
    scheduler = create_scheduler(bot)

    if settings.should_use_webhook() and settings.external_url:
        await run_webhook(bot, dispatcher, scheduler)
    else:
        await run_polling(bot, dispatcher, scheduler)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and "BOT_TOKEN" in str(exc):
            print(exc)
        else:
            print("تم إيقاف البوت.")

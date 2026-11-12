"""لوحة تحكم الأدمن — محمية بـ ADMIN_IDS ولا يدخلها إلا الأدمن."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot import keyboards as kb
from app.config import settings
from app.database import AdminLog, db_session
from app.providers.registry import PROVIDERS
from app.services import (
    price_check_service,
    price_history_service,
    settings_service,
    tracking_service,
    user_service,
)
from app.services.notification_service import count_notifications, recent_notifications
from app.utils.formatting import format_datetime, format_price, parse_number, short_text
from app.utils.logger import get_logger

logger = get_logger("admin_handlers")
router = Router()


class AdminStates(StatesGroup):
    waiting_user_search = State()
    waiting_interval = State()
    waiting_max_products = State()
    waiting_notable_drop = State()
    waiting_retention = State()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_list


def _log_admin_action(action: str, admin_id: int, target_user_id: int | None = None, details: str | None = None):
    with db_session() as session:
        session.add(AdminLog(action=action, admin_id=admin_id, target_user_id=target_user_id, details=details))


def _guard(callback: CallbackQuery) -> bool:
    """حماية لوحة الأدمن — أي مستخدم عادي يُرفض فورًا."""
    if not _is_admin(callback.from_user.id):
        logger.warning("Non-admin %s tried to access admin panel", callback.from_user.id)
        callback.answer("⛔ هذه اللوحة للأدمن فقط", show_alert=True)
        return False
    return True


# --------------------------------------------------------------------------
# القائمة والإحصائيات
# --------------------------------------------------------------------------

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ هذا الأمر للأدمن فقط.")
        return
    await _show_panel(message)


@router.callback_query(F.data == "admin:panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return
    await state.clear()
    await _show_panel(callback.message)
    await callback.answer()


async def _show_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 الإحصائيات", callback_data="admin:stats")],
        [InlineKeyboardButton(text="👥 إدارة المستخدمين", callback_data="admin:users")],
        [InlineKeyboardButton(text="📦 جميع المنتجات", callback_data="admin:products")],
        [InlineKeyboardButton(text="🔔 سجل التنبيهات", callback_data="admin:alerts")],
        [InlineKeyboardButton(text="⚙️ إعدادات البوت", callback_data="admin:settings")],
        [InlineKeyboardButton(text="🏪 المتاجر ومصادر البيانات", callback_data="admin:stores")],
        [InlineKeyboardButton(text="💾 النسخ الاحتياطي", callback_data="admin:backup")],
        [InlineKeyboardButton(text="📜 سجل العمليات", callback_data="admin:logs")],
    ])
    await message.answer("🛡️ <b>لوحة الأدمن</b>\n\nاختر من القائمة:", reply_markup=keyboard)


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not _guard(callback):
        return
    users = user_service.count_users()
    products = price_history_service.count_products()
    trackings = tracking_service.count_all_trackings(active_only=True)
    trackings_total = tracking_service.count_all_trackings(active_only=False)
    notifications = count_notifications()

    from app.database import PriceCheckLog
    from app.utils.formatting import localize

    cutoff = localize(format_datetime_now_minus_day())
    with db_session() as session:
        last_success = (
            session.query(PriceCheckLog)
            .filter(PriceCheckLog.status == "success")
            .order_by(PriceCheckLog.checked_at.desc())
            .first()
        )
        failed_checks = (
            session.query(PriceCheckLog)
            .filter(PriceCheckLog.status == "failed", PriceCheckLog.checked_at >= cutoff)
            .count()
        )

    text = (
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 المستخدمون: {users['total']} (نشط: {users['active']} | محظور: {users['blocked']})\n"
        f"📦 المنتجات: {products['total']} (Amazon: {products['amazon']} | Noon: {products['noon']})\n"
        f"🎯 المتابعات النشطة: {trackings} (الإجمالي: {trackings_total})\n\n"
        f"🔔 الإشعارات: مرسل {notifications['sent']} | معلق {notifications['pending']} | فاشل {notifications['failed']}\n"
        f"✅ آخر فحص ناجح: {format_datetime(last_success.checked_at) if last_success else 'لم يحدث بعد'}\n"
        f"❌ فحوصات فاشلة (آخر 24 ساعة): {failed_checks}\n"
    )
    await callback.message.answer(text)
    await callback.answer()


def format_datetime_now_minus_day():
    from app.utils.formatting import now
    return now() - timedelta(days=1)


# --------------------------------------------------------------------------
# إدارة المستخدمين
# --------------------------------------------------------------------------

@router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery):
    if not _guard(callback):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 قائمة المستخدمين", callback_data="admin:userslist")],
        [InlineKeyboardButton(text="🔍 بحث عن مستخدم", callback_data="admin:userssearch")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="admin:back")],
    ])
    await callback.message.answer("👥 <b>إدارة المستخدمين</b>", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin:userslist")
async def cb_admin_users_list(callback: CallbackQuery):
    if not _guard(callback):
        return
    users = user_service.list_users(limit=30)
    lines = ["📋 <b>المستخدمون (الأحدث أولًا):</b>", ""]
    for user in users:
        status = "🚫" if user.is_blocked else "✅"
        lines.append(
            f"{status} <code>{user.telegram_id}</code> — {user.first_name} {user.last_name} "
            f"(@{user.username}) — {user.created_at:%Y-%m-%d}"
        )
        lines.append(f"    إجراءات: /ban_{user.id} أو /unban_{user.id}")
    lines += ["", "💡 للحظر استخدم أمر <code>/ban_&lt;user_id&gt;</code> وللإلغاء <code>/unban_&lt;user_id&gt;</code>"]
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.message(F.text.regexp(r"^/(ban|unban)_(\d+)$"))
async def cmd_ban_user(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ هذا الأمر للأدمن فقط.")
        return
    action, user_db_id = message.text.lstrip("/").split("_")
    user_db_id = int(user_db_id)
    ok = user_service.set_blocked(user_db_id, blocked=(action == "ban"))
    if ok:
        _log_admin_action(f"user_{action}", message.from_user.id, target_user_id=user_db_id)
        await message.answer(f"✅ تم {'حظر' if action == 'ban' else 'إلغاء حظر'} المستخدم #{user_db_id}.")
    else:
        await message.answer("❌ المستخدم غير موجود.")


@router.callback_query(F.data == "admin:userssearch")
async def cb_admin_users_search(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return
    await state.set_state(AdminStates.waiting_user_search)
    await callback.message.answer("🔍 أرسل Telegram ID أو Username للمستخدم:")
    await callback.answer()


@router.message(AdminStates.waiting_user_search, F.text)
async def msg_user_search(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    users = user_service.find_users(message.text or "")
    await state.clear()
    if not users:
        await message.answer("❌ لم يتم العثور على مستخدم.")
        return
    for user in users:
        trackings = tracking_service.get_user_trackings(user.telegram_id)
        text = (
            f"👤 <b>{user.first_name} {user.last_name}</b> (@{user.username})\n"
            f"🆔 <code>{user.telegram_id}</code>\n"
            f"📅 سجل في: {format_datetime(user.created_at)}\n"
            f"{'🚫 محظور' if user.is_blocked else '✅ نشط'}\n"
            f"📦 المتابعات ({len(trackings)}):\n"
        )
        for tracking in trackings[:10]:
            text += f"  • {short_text(tracking.product.title, 40)}\n"
        text += f"\nإجراءات: /ban_{user.id} أو /unban_{user.id}"
        await message.answer(text)


# --------------------------------------------------------------------------
# المنتجات وسجل التنبيهات
# --------------------------------------------------------------------------

@router.callback_query(F.data == "admin:products")
async def cb_admin_products(callback: CallbackQuery):
    if not _guard(callback):
        return
    products = price_history_service.list_products(limit=30)
    trackings = tracking_service.count_all_trackings(active_only=False)
    lines = [f"📦 <b>جميع المنتجات ({price_history_service.count_products()['total']}) — المتابعات: {trackings}</b>", ""]
    for product in products[:30]:
        lines.append(
            f"• {short_text(product.title, 45)}\n"
            f"  {product.store} | 💰 {format_price(product.current_price)} | "
            f"آخر فحص: {format_datetime(product.last_checked_at)}"
        )
    await callback.message.answer("\n".join(lines) or "لا توجد منتجات")
    await callback.answer()


@router.callback_query(F.data == "admin:alerts")
async def cb_admin_alerts(callback: CallbackQuery):
    if not _guard(callback):
        return
    notifications = recent_notifications(limit=20)
    stats = count_notifications()
    lines = [
        f"🔔 <b>سجل التنبيهات</b> (إجمالي: {stats['total']} | مرسل: {stats['sent']})",
        "",
    ]
    for notification in notifications[:20]:
        lines.append(
            f"• [{notification.status}] {notification.notification_type} — "
            f"سعر {format_price(notification.price_at_notification)} — {format_datetime(notification.created_at)}"
        )
    await callback.message.answer("\n".join(lines) or "لا توجد إشعارات")
    await callback.answer()


# --------------------------------------------------------------------------
# إعدادات البوت
# --------------------------------------------------------------------------

@router.callback_query(F.data == "admin:settings")
async def cb_admin_settings(callback: CallbackQuery):
    if not _guard(callback):
        return
    all_settings = settings_service.get_all_settings()
    lines = ["⚙️ <b>إعدادات البوت الحالية:</b>", ""]
    for key, value in all_settings.items():
        lines.append(f"• <code>{key}</code>: <b>{value}</b>")
    lines += [
        "",
        "أوامر التعديل:",
        "• <code>/set_check_interval_hours 6</code> — فترة الفحص\n"
        "• <code>/set_max_products_per_user 20</code>\n"
        "• <code>/set_notable_drop_percentage 15</code>\n"
        "• <code>/set_price_history_retention_days 90</code>\n"
        "• <code>/toggle_admin_notifications</code> — إشعارات الأدمن\n"
        "• <code>/toggle_alerts</code> — تشغيل/إيقاف التنبيهات\n"
        "• <code>/toggle_amazon</code> / <code>/toggle_noon</code> — المتاجر",
    ]
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "admin:back")
async def cb_admin_back(callback: CallbackQuery):
    if not _guard(callback):
        return
    await _show_panel(callback.message)
    await callback.answer()


@router.message(F.text.regexp(r"^/set_(\w+) (-?[\d.]+)$"))
async def cmd_set_setting(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ هذا الأمر للأدمن فقط.")
        return
    key, value = message.text.lstrip("/").removeprefix("set_").split(" ", 1)
    try:
        parsed = float(value)
    except ValueError:
        await message.answer("❌ قيمة غير صالحة.")
        return
    if settings_service.set_setting(key, parsed if "." not in str(value) else parsed):
        _log_admin_action("setting_changed", message.from_user.id, details=f"{key}={value}")
        await message.answer(f"✅ تم تحديث <code>{key}</code> = {value}")
    else:
        await message.answer(f"❌ إعداد غير معروف: <code>{key}</code>")


@router.message(F.text.regexp(r"^/toggle_(\w+)$"))
async def cmd_toggle_setting(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ هذا الأمر للأدمن فقط.")
        return
    key = message.text.lstrip("/").removeprefix("toggle_")
    key_map = {
        "admin_notifications": "admin_notifications_enabled",
        "alerts": "alerts_enabled",
        "amazon": "store_amazon_enabled",
        "noon": "store_noon_enabled",
    }
    setting_key = key_map.get(key)
    if not setting_key:
        await message.answer(f"❌ إعداد غير معروف: <code>{key}</code>")
        return
    current = settings_service.get_setting(setting_key)
    settings_service.set_setting(setting_key, not current)
    _log_admin_action("setting_toggled", message.from_user.id, details=f"{setting_key}={not current}")
    await message.answer(f"✅ <code>{setting_key}</code> أصبح: <b>{'مفعل' if not current else 'متوقف'}</b>")


# --------------------------------------------------------------------------
# المتاجر ومصادر البيانات
# --------------------------------------------------------------------------

@router.callback_query(F.data == "admin:stores")
async def cb_admin_stores(callback: CallbackQuery):
    if not _guard(callback):
        return
    lines = ["🏪 <b>المتاجر وحالة مصادر البيانات:</b>", ""]
    for provider in PROVIDERS:
        enabled = settings_service.is_store_enabled(provider.store_key)
        lines.append(
            f"• <b>{provider.store_display_name}</b>\n"
            f"  الحالة: {'🟢 مفعل' if enabled else '🔴 متوقف'}\n"
            f"  مصدر البيانات: صفحة المنتج العامة ({provider.store_key}_public_page)\n"
        )
    lines += [
        "⚠️ ملاحظة: إذا عجز المصدر عن قراءة السعر (حماية/تعديل صفحة) سيُسجل الفشل في سجل الفحوصات",
        "ولن يعرض البوت أي سعر غير حقيقي.",
        "",
        "للتبديل: <code>/toggle_amazon</code> أو <code>/toggle_noon</code>",
    ]
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "admin:backup")
async def cb_admin_backup(callback: CallbackQuery):
    if not _guard(callback):
        return
    from datetime import datetime
    from pathlib import Path

    from app.database.database import backup_database

    backup_dir = Path(settings.database_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"backup_{datetime.now():%Y%m%d_%H%M%S}.db"
    ok = backup_database(backup_path)
    _log_admin_action("backup", callback.from_user.id, details=str(backup_path) if ok else "failed")
    await callback.message.answer(
        f"💾 {'✅ تم إنشاء نسخة احتياطية:' if ok else '❌ فشل النسخ الاحتياطي'}\n<code>{backup_path}</code>"
        if ok else "❌ فشل النسخ الاحتياطي — راجع السجلات."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:logs")
async def cb_admin_logs(callback: CallbackQuery):
    if not _guard(callback):
        return
    with db_session() as session:
        logs = session.query(AdminLog).order_by(AdminLog.created_at.desc()).limit(30).all()
    lines = ["📜 <b>آخر عمليات الأدمن:</b>", ""]
    for log in logs:
        lines.append(f"• {format_datetime(log.created_at)} — {log.action} — {log.details or ''}")
    await callback.message.answer("\n".join(lines) or "لا توجد عمليات")
    await callback.answer()

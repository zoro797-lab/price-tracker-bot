"""معالجات القوائم الرئيسية ومنتجاتي والتفاصيل — المستخدم العادي."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts as t
from app.bot.display import product_card_text, product_detail_text, history_text
from app.config import settings
from app.database import UserTracking
from app.services import price_history_service, settings_service, tracking_service, user_service
from app.utils.formatting import format_price, short_text
from app.utils.logger import get_logger

logger = get_logger("user_handlers")
router = Router()


# --------------------------------------------------------------------------
# البداية والمساعدة
# --------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user_record, is_new_user: bool, is_admin: bool):
    await state.clear()
    if is_new_user:
        await _notify_admins_new_user(message.bot, user_record)
    await message.answer(t.WELCOME, reply_markup=kb.main_menu_keyboard(is_admin))


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, is_admin: bool):
    await state.clear()
    await message.answer(t.MAIN_MENU_INLINE, reply_markup=kb.main_menu_keyboard(is_admin))


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(t.HELP, reply_markup=kb.back_to_main_keyboard())


@router.callback_query(F.data == "menu:main")
async def cb_main(callback: CallbackQuery, state: FSMContext, is_admin: bool):
    await state.clear()
    await callback.message.edit_text(t.MAIN_MENU_INLINE, reply_markup=kb.main_menu_keyboard(is_admin))
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(t.HELP, reply_markup=kb.back_to_main_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:add")
async def cb_menu_add(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(t.SEND_URL, reply_markup=kb.cancel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:settings")
async def cb_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    count = tracking_service.count_user_trackings(callback.from_user.id)
    user = user_service.get_user_by_telegram_id(callback.from_user.id)
    interval = settings_service.get_setting("check_interval_hours")
    text = (
        "⚙️ <b>إعداداتي</b>\n\n"
        f"👤 الاسم: {user.first_name or ''} {user.last_name or ''}\n"
        f"🆔 Telegram ID: <code>{callback.from_user.id}</code>\n"
        f"📦 عدد المتابعات النشطة: {count}\n"
        f"🕐 فترة الفحص الحالية: كل {interval} ساعة\n"
        f"📅 تاريخ التسجيل: {user.created_at:%Y-%m-%d}\n\n"
        "💡 إعدادات التنبيه لكل منتج تُعدل من صفحة المنتج داخل «منتجاتي».\n"
        "🔔 لضبط تنبيهات التحسن الإضافي، عدّل التنبيه من صفحة المنتج."
    )
    await callback.message.edit_text(text, reply_markup=kb.back_to_main_keyboard())
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext, is_admin: bool):
    await state.clear()
    await callback.message.edit_text(t.CANCELLED, reply_markup=kb.main_menu_keyboard(is_admin))
    await callback.answer("تم الإلغاء")


# --------------------------------------------------------------------------
# منتجاتي المتابعة
# --------------------------------------------------------------------------

def _tracking_list_item(tracking: UserTracking) -> str:
    """سطر مضغوط لبطاقة المنتج داخل القائمة (نقطة 8)."""
    product = tracking.product
    status_icon = "🟢" if tracking.is_active else "⏸️"
    alert_desc = ""
    if tracking.alert_type == "price_range":
        alert_desc = f"نطاق {format_price(tracking.target_min_price)} - {format_price(tracking.target_max_price)}"
    elif tracking.alert_type == "max_price":
        alert_desc = f"أقصى {format_price(tracking.target_price)}"
    elif tracking.alert_type == "percentage_drop":
        alert_desc = f"انخفاض {tracking.target_drop_percentage:.0f}%"
    last_alert = (
        f"آخر إشعار: {format_price(tracking.last_alerted_price)}"
        if tracking.last_alerted_price is not None else "لا إشعارات بعد"
    )
    return (
        f"{status_icon} <b>{short_text(product.title, 45)}</b>\n"
        f"   💰 {format_price(product.current_price)} | 🎯 {alert_desc}\n"
        f"   🔔 {last_alert}"
    )


@router.callback_query(F.data == "menu:myproducts")
async def cb_my_products(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    trackings = tracking_service.get_user_trackings(callback.from_user.id, include_inactive=True)
    if not trackings:
        await callback.message.edit_text(t.MY_PRODUCTS_EMPTY, reply_markup=kb.back_to_main_keyboard())
        await callback.answer()
        return

    lines = [t.MY_PRODUCTS_HEADER.format(count=len(trackings)), ""]
    for index, tracking in enumerate(trackings, start=1):
        lines.append(f"{index}. {_tracking_list_item(tracking)}")
        lines.append("")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_products_list_keyboard(trackings),
    )
    await callback.answer()


def _products_list_keyboard(trackings: list[UserTracking]):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for tracking in trackings:
        label = f"📄 {short_text(tracking.product.title, 35)}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"detail:{tracking.id}")])
    rows.append([InlineKeyboardButton(text="➕ إضافة منتج جديد", callback_data="menu:add")])
    rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("detail:"))
async def cb_product_detail(callback: CallbackQuery):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    await callback.message.edit_text(
        product_detail_text(tracking),
        reply_markup=kb.product_card_keyboard(tracking),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("stoptrack:"))
async def cb_stop_tracking(callback: CallbackQuery):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    tracking_service.set_tracking_active(tracking_id, active=False)
    await callback.answer("⏸️ تم إيقاف المتابعة")
    await cb_product_detail(callback)


@router.callback_query(F.data.startswith("resumetrack:"))
async def cb_resume_tracking(callback: CallbackQuery):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    tracking_service.set_tracking_active(tracking_id, active=True)
    await callback.answer("▶️ تم استئناف المتابعة وإعادة تسليح التنبيه")
    await cb_product_detail(callback)


@router.callback_query(F.data.startswith("deltrack:"))
async def cb_delete_tracking(callback: CallbackQuery):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    await callback.message.edit_text(
        "⚠️ <b>تأكيد الحذف</b>\n\n"
        f"هل أنت متأكد من حذف متابعة:\n<b>{short_text(tracking.product.title, 60)}</b>؟\n\n"
        "💡 سجل الأسعار التاريخي للمنتج سيُحتفظ به إن كان متابَعًا من مستخدم آخر.",
        reply_markup=kb.confirm_delete_keyboard(tracking_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deltrackyes:"))
async def cb_delete_tracking_yes(callback: CallbackQuery, is_admin: bool):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    title = short_text(tracking.product.title, 50)
    tracking_service.delete_tracking(tracking_id)
    await callback.message.edit_text(
        f"🗑️ تم حذف متابعة: <b>{title}</b>",
        reply_markup=kb.main_menu_keyboard(is_admin),
    )
    await callback.answer("تم الحذف")


# --------------------------------------------------------------------------
# سجل الأسعار (اختيار منتج ثم عرض)
# --------------------------------------------------------------------------

@router.callback_query(F.data == "menu:history")
async def cb_menu_history(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    trackings = tracking_service.get_user_trackings(callback.from_user.id, include_inactive=False)
    if not trackings:
        await callback.message.edit_text(t.MY_PRODUCTS_EMPTY, reply_markup=kb.back_to_main_keyboard())
        await callback.answer()
        return
    await callback.message.edit_text(
        "📊 اختر المنتج لعرض سجل أسعاره:",
        reply_markup=_products_list_keyboard(trackings),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("history:"))
async def cb_history(callback: CallbackQuery):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await callback.message.edit_text(
        history_text(tracking),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📈 عرض الرسم البياني", callback_data=f"chartmenu:{tracking_id}")],
            [InlineKeyboardButton(text="↩️ رجوع للمنتج", callback_data=f"detail:{tracking_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("chartmenu:"))
async def cb_chart_menu(callback: CallbackQuery):
    tracking_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(t.CHART_PERIOD_PROMPT, reply_markup=kb.chart_period_keyboard(tracking_id))
    await callback.answer()


# --------------------------------------------------------------------------
# إشعارات الأدمن الداخلية
# --------------------------------------------------------------------------

async def _notify_admins_new_user(bot: Bot, user_record) -> None:
    from app.database import db_session
    from app.services.settings_service import get_setting

    if not get_setting("admin_notifications_enabled"):
        return
    message = (
        "👤 <b>مستخدم جديد سجل في البوت</b>\n\n"
        f"👤 الاسم: {user_record.first_name} {user_record.last_name}\n"
        f"🆔 Telegram ID: <code>{user_record.telegram_id}</code>\n"
        f"📱 Username: " + (f"@{user_record.username}" if user_record.username else "—")
    )
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
        except Exception as exc:
            logger.warning("Failed notifying admin %s: %s", admin_id, exc)

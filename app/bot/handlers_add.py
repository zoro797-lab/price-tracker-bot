"""تدفق إضافة منتج + تحديد نوع التنبيه (نطاق / أقصى / نسبة انخفاض) + التأكيد.

التسلسل:
1. المستخدم يرسل الرابط → التحقق → قراءة المنتج من المصدر → عرض بطاقة المنتج.
2. اختيار نوع التنبيه: نطاق سعري / سعر أقصى / نسبة انخفاض.
3. إدخال القيم (مع التحقق: الحد الأدنى ≤ الحد الأعلى، النسبة 1-99...).
4. سؤال اختياري: تنبيه التحسن الإضافي.
5. ملخص + تأكيد → تسجيل المتابعة + إشعار الأدمن + أول فحص فوري.
"""
from __future__ import annotations

import asyncio
import re

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts as t
from app.config import settings
from app.providers import (
    BlockedByStore,
    ProductSnapshot,
    SourceUnavailable,
    UnsupportedURL,
    get_provider_for_url,
    validate_url_safety,
)
from app.services import (
    alert_service,
    price_history_service,
    product_service,
    settings_service,
    tracking_service,
    user_service,
)
from app.services.alert_evaluator import AlertType
from app.bot.states import AddProductStates, EditAlertStates
from app.utils.formatting import format_price, parse_number, short_text
from app.utils.logger import get_logger

logger = get_logger("add_handlers")
router = Router()

_EDIT_STATE_BY_STEP = {
    "type": EditAlertStates.waiting_alert_type,
    "min": EditAlertStates.waiting_min_price,
    "max": EditAlertStates.waiting_max_price,
    "maxonly": EditAlertStates.waiting_max_price_only,
    "drop": EditAlertStates.waiting_drop_percentage,
    "ref": EditAlertStates.waiting_reference_price,
    "additional": EditAlertStates.waiting_additional_drop,
}

_URL_RE = re.compile(r"https?://\S+")
_STORE_RE = re.compile(r"amazon\.eg|noon\.com")


class SupportedStoreUrl(BaseFilter):
    """فلتر بحثي: يستخرج أول رابط من الرسالة ويتحقق أنه لمتجر مدعوم.

    يعيد dict {store_url: ...} فيحقن قيمة الرابط في معاملات المعالج مباشرة.
    يستخدم re.search (وليس regexp المدمج الذي يعمل بأسلوب re.match المثبت
    على بداية النص فيفشل عندما يكون الرابط داخل نص).
    """

    async def __call__(self, message: Message) -> dict | bool:
        text = message.text or message.caption or ""
        match = _URL_RE.search(text)
        if not match:
            return False
        url = match.group(0).rstrip(".,;:!؟)…«»\"'")  # تنظيف علامات ترقيم من نهاية الرابط
        if _STORE_RE.search(url):
            return {"store_url": url}
        return False


# --------------------------------------------------------------------------
# أدوات مساعدة للدفق المشترك
# --------------------------------------------------------------------------

async def _flow_prefix(state: FSMContext) -> str:
    """بادئة أزرار الدفق: add أو edit{tracking_id}."""
    data = await state.get_data()
    if data.get("tracking_id") is not None:
        return f"edit{data['tracking_id']}"
    return "add"


# --------------------------------------------------------------------------
# استقبال الرابط
# --------------------------------------------------------------------------

async def start_add_flow(bot: Bot, chat_id: int, state: FSMContext, url: str) -> None:
    """التحقق من الرابط وقراءة المنتج ثم عرض بطاقة المنتج وطلب نوع التنبيه."""
    error = validate_url_safety(url)
    if error:
        await bot.send_message(chat_id, f"❌ {error}")
        return

    try:
        provider = get_provider_for_url(url)
    except UnsupportedURL:
        await bot.send_message(chat_id, t.UNSUPPORTED_STORE)
        return

    if not settings_service.is_store_enabled(provider.store_key):
        await bot.send_message(chat_id, t.STORE_DISABLED.format(store=provider.store_display_name))
        return

    status = await bot.send_message(chat_id, "⏳ جاري قراءة المنتج من المصدر...")
    try:
        snapshot: ProductSnapshot = await asyncio.to_thread(provider.fetch_product, url)
    except BlockedByStore as exc:
        await status.edit_text(t.FETCH_FAILED.format(reason=exc))
        await state.clear()
        return
    except (SourceUnavailable, UnsupportedURL) as exc:
        await status.edit_text(t.FETCH_FAILED.format(reason=exc))
        await state.clear()
        return

    # بطاقة المنتج قبل تحديد التنبيه
    caption = (
        f"📦 <b>{short_text(snapshot.title, 80)}</b>\n"
        f"🏪 المتجر: {provider.store_display_name}\n"
        f"💰 السعر الحالي: <b>{format_price(snapshot.price)}</b>\n"
        f"🛒 التوفر: {'✅ متاح' if snapshot.availability.value == 'available' else '❌ غير متاح' if snapshot.availability.value == 'unavailable' else '❔ غير معروف'}\n"
        f"🔗 <a href=\"{url}\">فتح المنتج</a>\n\n"
        + t.CHOOSE_ALERT_TYPE
    )
    await state.update_data(url=url, snapshot_title=snapshot.title, snapshot_price=snapshot.price,
                            snapshot_currency=snapshot.currency)
    await status.edit_text(caption, reply_markup=kb.alert_type_keyboard("add"))
    await state.set_state(AddProductStates.waiting_alert_type)


@router.message(SupportedStoreUrl())
async def message_with_url(message: Message, state: FSMContext, store_url: str):
    """أي رسالة تحتوي رابط متجر مدعوم تبدأ تدفق الإضافة مباشرة.

    ملاحظة: نستخدم فلتر مخصص بحثي (re.search) — فلتر aiogram المدمج
    F.text.regexp يعمل بأسلوب re.match (من بداية النص) فيفشل عندما
    يكون الرابط داخل نص أطول.
    """
    if await state.get_state() is not None:
        return  # داخل حالة أخرى — لا تتدخل
    logger.info("Received store URL from user %s: %s", message.from_user.id if message.from_user else '?', store_url)
    await start_add_flow(message.bot, message.chat.id, state, store_url)


# --------------------------------------------------------------------------
# اختيار نوع التنبيه (يخدم تدفقَي الإضافة والتعديل)
# --------------------------------------------------------------------------

def _parse_flow_callback(callback_data: str) -> tuple[str, int | None, list[str]]:
    """تفكيك callback: add:type:... أو edit{tid}:type:..."""
    first, *rest = callback_data.split(":")
    if first == "add":
        return "add", None, rest
    if first.startswith("edit"):
        return "edit", int(first[4:]), rest
    return first, None, rest


async def _ask_first_value_for_type(message, state: FSMContext, prefix: str, alert_type: str):
    """بعد اختيار النوع — السؤال الأول حسب النوع."""
    data = await state.get_data()
    await state.update_data(alert_type=alert_type)

    if alert_type == AlertType.PRICE_RANGE.value:
        await message.edit_text(t.RANGE_MIN_PROMPT, reply_markup=kb.cancel_keyboard())
        state_cls = AddProductStates if prefix == "add" else EditAlertStates
        await state.set_state(state_cls.waiting_min_price)
    elif alert_type == AlertType.MAX_PRICE.value:
        await message.edit_text(t.MAX_PRICE_PROMPT, reply_markup=kb.cancel_keyboard())
        state_cls = AddProductStates if prefix == "add" else EditAlertStates
        await state.set_state(state_cls.waiting_max_price_only)
    else:  # percentage_drop
        await message.edit_text(t.DROP_PERCENTAGE_PROMPT, reply_markup=kb.cancel_keyboard())
        state_cls = AddProductStates if prefix == "add" else EditAlertStates
        await state.set_state(state_cls.waiting_drop_percentage)


@router.callback_query(F.data.regexp(r"^(add|edit\d+):type:(price_range|max_price|percentage_drop)$"))
async def cb_alert_type_chosen(callback: CallbackQuery, state: FSMContext):
    flow, tracking_id, rest = _parse_flow_callback(callback.data)
    alert_type = rest[1]

    if flow == "edit":
        tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
        if tracking is None:
            await callback.answer(t.NO_ACCESS, show_alert=True)
            return
        await state.update_data(tracking_id=tracking_id, product_id=tracking.product_id)

    if flow == "add" and (await state.get_data()).get("snapshot_price") is None:
        await callback.answer("ابدأ بإرسال رابط المنتج أولًا", show_alert=True)
        return

    await _ask_first_value_for_type(callback.message, state, flow, alert_type)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^(add|edit\d+):backtotype$"))
async def cb_back_to_type(callback: CallbackQuery, state: FSMContext):
    flow, tracking_id, _ = _parse_flow_callback(callback.data)
    if flow == "edit":
        tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
        if tracking is None:
            await callback.answer(t.NO_ACCESS, show_alert=True)
            return
    state_cls = AddProductStates if flow == "add" else EditAlertStates
    await callback.message.edit_text(t.CHOOSE_ALERT_TYPE, reply_markup=kb.alert_type_keyboard(flow))
    await state.set_state(state_cls.waiting_alert_type)
    await callback.answer()


# --------------------------------------------------------------------------
# إدخالات الأرقام — النطاق السعري
# --------------------------------------------------------------------------

@router.message(F.text, AddProductStates.waiting_min_price, EditAlertStates.waiting_min_price)
async def msg_min_price(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or value <= 0:
        await message.answer(t.INVALID_NUMBER)
        return

    data = await state.get_data()
    edit_mode = data.get("edit_mode")

    if edit_mode:  # تعديل سريع للحد الأدنى فقط
        await _apply_quick_edit(message, state, "min", value)
        return

    await state.update_data(min_price=value)
    await message.answer(
        t.RANGE_MAX_PROMPT.format(min_price=format_price(value)),
        reply_markup=kb.cancel_keyboard(),
    )
    await state.set_state(AddProductStates.waiting_max_price)


@router.message(F.text, AddProductStates.waiting_max_price, EditAlertStates.waiting_max_price)
async def msg_max_price(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or value <= 0:
        await message.answer(t.INVALID_NUMBER)
        return

    data = await state.get_data()
    edit_mode = data.get("edit_mode")

    if edit_mode:  # تعديل سريع للحد الأعلى فقط
        await _apply_quick_edit(message, state, "max", value)
        return

    min_price = data.get("min_price")
    if min_price is not None and value < min_price:
        # شرط أساسي: الحد الأدنى أقل من أو يساوي الحد الأعلى
        await message.answer(t.MIN_GREATER_THAN_MAX)
        return

    await state.update_data(max_price=value)
    await message.answer(t.ADDITIONAL_DROP_PROMPT, reply_markup=kb.skip_or_cancel_keyboard(await _flow_prefix(state)))
    await state.set_state(AddProductStates.waiting_additional_drop)


# --------------------------------------------------------------------------
# إدخالات الأرقام — السعر الأقصى
# --------------------------------------------------------------------------

@router.message(F.text, AddProductStates.waiting_max_price_only, EditAlertStates.waiting_max_price_only)
async def msg_max_price_only(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or value <= 0:
        await message.answer(t.INVALID_NUMBER)
        return

    data = await state.get_data()
    if data.get("edit_mode"):  # تعديل سريع للسعر الأقصى فقط
        await _apply_quick_edit(message, state, "maxonly", value)
        return

    await state.update_data(target_price=value)
    await message.answer(t.ADDITIONAL_DROP_PROMPT, reply_markup=kb.skip_or_cancel_keyboard(await _flow_prefix(state)))
    await state.set_state(AddProductStates.waiting_additional_drop)


# --------------------------------------------------------------------------
# إدخالات الأرقام — نسبة الانخفاض والسعر المرجعي
# --------------------------------------------------------------------------

@router.message(F.text, AddProductStates.waiting_drop_percentage, EditAlertStates.waiting_drop_percentage)
async def msg_drop_percentage(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or not (1 <= value <= 99):
        await message.answer(t.INVALID_PERCENTAGE)
        return

    data = await state.get_data()
    if data.get("edit_mode"):  # تعديل سريع للنسبة فقط
        await _apply_quick_edit(message, state, "drop", value)
        return

    await state.update_data(drop_percentage=value)
    current_price = data.get("snapshot_price")
    prefix = await _flow_prefix(state)
    await message.answer(t.DROP_REFERENCE_PROMPT, reply_markup=kb.reference_choice_keyboard(prefix, current_price or 0))
    await state.set_state(AddProductStates.waiting_reference_choice)


@router.callback_query(F.data.regexp(r"^(add|edit\d+):ref:(addtime|custom)$"))
async def cb_reference_choice(callback: CallbackQuery, state: FSMContext):
    flow, tracking_id, rest = _parse_flow_callback(callback.data)
    choice = rest[1]

    data = await state.get_data()
    edit_mode = data.get("edit_mode")

    if choice == "addtime":
        if flow == "add":
            reference = data.get("snapshot_price")
        else:
            # التعديل: السعر الحالي وقت تعديل إعداد التنبيه
            tracking = tracking_service.get_tracking(tracking_id)
            reference = tracking.product.current_price if tracking else None

        if reference is None:
            await callback.answer("لا يوجد سعر حالي — أدخل السعر يدويًا", show_alert=True)
            return

        if edit_mode == "ref":  # تعديل سريع للسعر المرجعي بالسعر الحالي
            await _apply_quick_edit(callback.message, state, "ref", reference)
            await callback.answer()
            return

        await state.update_data(reference_price=reference)
        await callback.message.answer(
            f"📌 السعر المرجعي: <b>{format_price(reference)}</b>\n\n" + t.ADDITIONAL_DROP_PROMPT,
            reply_markup=kb.skip_or_cancel_keyboard(await _flow_prefix(state)),
        )
        state_cls = AddProductStates if flow == "add" else EditAlertStates
        await state.set_state(state_cls.waiting_additional_drop)
        await callback.answer()
    else:
        if edit_mode == "ref":
            # تعديل سريع: إدخال سعر مرجعي يدوي
            await state.update_data(edit_mode="ref")
        await callback.message.answer(t.DROP_REFERENCE_CUSTOM_PROMPT, reply_markup=kb.cancel_keyboard())
        state_cls = AddProductStates if flow == "add" else EditAlertStates
        await state.set_state(state_cls.waiting_reference_price)
        await callback.answer()


@router.message(F.text, AddProductStates.waiting_reference_price, EditAlertStates.waiting_reference_price)
async def msg_reference_price(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or value <= 0:
        await message.answer(t.INVALID_REFERENCE)
        return

    data = await state.get_data()
    if data.get("edit_mode") == "ref":
        await _apply_quick_edit(message, state, "ref", value)
        return

    await state.update_data(reference_price=value)
    await message.answer(t.ADDITIONAL_DROP_PROMPT, reply_markup=kb.skip_or_cancel_keyboard(await _flow_prefix(state)))
    await state.set_state(AddProductStates.waiting_additional_drop)


# --------------------------------------------------------------------------
# تنبيه التحسن الإضافي (اختياري)
# --------------------------------------------------------------------------

@router.callback_query(F.data.regexp(r"^(add|edit\d+):skipadditional$"))
async def cb_skip_additional(callback: CallbackQuery, state: FSMContext):
    prefix = callback.data.split(":")[0]
    await state.update_data(notify_additional=False, additional_drop=None)
    await _show_summary(callback.message, state, prefix)
    await callback.answer()


@router.message(F.text, AddProductStates.waiting_additional_drop, EditAlertStates.waiting_additional_drop)
async def msg_additional_drop(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or not (1 <= value <= 99):
        await message.answer(t.INVALID_PERCENTAGE)
        return
    await state.update_data(notify_additional=True, additional_drop=value)
    await _show_summary(message, state, await _flow_prefix(state))


# --------------------------------------------------------------------------
# الملخص والتأكيد
# --------------------------------------------------------------------------

def _build_summary_text(state_data: dict, alert_type: str, product_title: str, current_price: float) -> str:
    """ملخص المتابعة قبل التأكيد — مطابق لتجربة التحديد المتدرجة في المواصفات."""
    lines = [
        t.CONFIRM_HEADER,
        "",
        f"📦 المنتج: {short_text(product_title, 60)}",
        f"💰 السعر الحالي: {format_price(current_price)}",
        f"🎯 نوع التنبيه: {alert_service.ALERT_TYPE_NAMES.get(alert_type, alert_type)}",
    ]
    if alert_type == AlertType.PRICE_RANGE.value:
        lines += [
            f"📉 من: {format_price(state_data.get('min_price'))}",
            f"📈 إلى: {format_price(state_data.get('max_price'))}",
            "",
            t.CONFIRM_NOTE_RANGE.format(max=format_price(state_data.get("max_price"))),
        ]
    elif alert_type == AlertType.MAX_PRICE.value:
        lines += [
            f"🔝 السعر الأقصى: {format_price(state_data.get('target_price'))}",
            "",
            "🔔 سيتم إرسال إشعار عندما يصبح السعر " + format_price(state_data.get("target_price")) + " أو أقل.",
        ]
    else:
        reference = state_data.get("reference_price")
        percentage = state_data.get("drop_percentage")
        threshold = round(reference * (1 - percentage / 100), 2) if reference and percentage else None
        lines += [
            f"📉 نسبة الانخفاض المطلوبة: {percentage:.0f}%",
            f"📌 السعر المرجعي: {format_price(reference)}",
            f"🧮 طريقة الحساب: {format_price(reference)} × (1 - {percentage:.0f}%) = {format_price(threshold)}",
            "",
            f"🔔 سيتم إرسال إشعار عندما يصبح السعر {format_price(threshold)} أو أقل.",
        ]

    if state_data.get("notify_additional"):
        lines.append(f"➕ تنبيه تحسن إضافي: عند انخفاض إضافي {state_data.get('additional_drop'):.0f}% عن سعر آخر تنبيه")
    return "\n".join(lines)


async def _show_summary(message, state: FSMContext, prefix: str):
    """عرض ملخص المتابعة — prefix = add أو edit{tracking_id}."""
    data = await state.get_data()
    flow = "edit" if prefix.startswith("edit") else "add"
    if flow == "add":
        product_title = data.get("snapshot_title", "")
        current_price = data.get("snapshot_price")
    else:
        from app.services.product_service import get_product
        product = get_product(data.get("product_id"))
        product_title = product.title if product else ""
        current_price = product.current_price if product else None

    text = _build_summary_text(data, data.get("alert_type"), product_title, current_price)
    state_cls = AddProductStates if flow == "add" else EditAlertStates
    try:
        await message.edit_text(text, reply_markup=kb.confirm_keyboard(prefix))
    except Exception:
        await message.answer(text, reply_markup=kb.confirm_keyboard(prefix))
    await state.set_state(state_cls.confirm_summary)


@router.callback_query(F.data.regexp(r"^(add|edit\d+):confirm$"))
async def cb_confirm(callback: CallbackQuery, state: FSMContext):
    flow, tracking_id, _ = _parse_flow_callback(callback.data)
    data = await state.get_data()
    alert_type = data.get("alert_type")

    if flow == "add":
        await _finish_add(callback, state, data, alert_type)
    else:
        await _finish_edit(callback, state, tracking_id, data, alert_type)


async def _finish_add(callback: CallbackQuery, state: FSMContext, data: dict, alert_type: str):
    """تسجيل المتابعة الجديدة + إشعار الأدمن + أول فحص فوري."""
    telegram_id = callback.from_user.id
    user = user_service.get_user_by_telegram_id(telegram_id)

    # حد أقصى للمنتجات لكل مستخدم
    limit = settings_service.get_setting("max_products_per_user")
    if tracking_service.count_user_trackings(telegram_id) >= limit:
        await callback.message.edit_text(t.TRACKING_LIMIT.format(limit=limit))
        await state.clear()
        await callback.answer()
        return

    # إعادة قراءة المنتج للتأكد من أن المتابعة تبدأ بقراءة سعر حقيقية
    provider = get_provider_for_url(data["url"])
    try:
        snapshot = await asyncio.to_thread(provider.fetch_product, data["url"])
    except Exception as exc:
        await callback.message.edit_text(t.FETCH_FAILED.format(reason=exc))
        await state.clear()
        await callback.answer()
        return

    product, is_new_product = product_service.get_or_create_product(snapshot, data["url"])
    # سجل أول قراءة في السجل التاريخي
    price_history_service.record_price(
        product.id, snapshot.price, snapshot.currency, snapshot.data_source, snapshot.availability.value,
        min_gap_minutes=0,
    )

    tracking = tracking_service.create_tracking(
        user=user,
        product=product,
        alert_type=alert_type,
        target_min_price=data.get("min_price"),
        target_max_price=data.get("max_price"),
        target_price=data.get("target_price"),
        target_drop_percentage=data.get("drop_percentage"),
        reference_price=data.get("reference_price"),
        notify_on_additional_drop=bool(data.get("notify_additional")),
        additional_drop_percentage=data.get("additional_drop"),
    )

    # إشعار الأدمن بنوع التنبيه والنطاق المحدد (نقطة 9)
    if settings_service.get_setting("admin_notifications_enabled"):
        admin_message = alert_service.build_admin_new_product_message(user, product, tracking)
        for admin_id in settings.admin_id_list:
            try:
                await callback.bot.send_message(chat_id=admin_id, text=admin_message)
            except Exception as exc:
                logger.warning("Failed notifying admin %s: %s", admin_id, exc)

    # أول فحص فوري: إذا الشرط متحقق من البداية أرسل إشعارًا الآن (نقطة 6)
    decision, alert_message = alert_service.evaluate_initial_alert(tracking, product, snapshot.price)
    if alert_message:
        try:
            await callback.bot.send_message(
                chat_id=telegram_id,
                text=alert_message,
                reply_markup=alert_service.build_alert_keyboard(tracking),
            )
        except Exception as exc:
            logger.error("Initial alert send failed: %s", exc)

    await state.clear()
    await callback.message.edit_text(t.TRACKING_STARTED, reply_markup=kb.main_menu_keyboard(False))
    await callback.answer("تم بدء المتابعة")


async def _finish_edit(callback: CallbackQuery, state: FSMContext, tracking_id: int, data: dict, alert_type: str):
    """تعديل التنبيه دون حذف المنتج — وإعادة ضبط حالة التنبيه بشكل صحيح."""
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return

    updated = tracking_service.update_tracking_alert(
        tracking_id,
        alert_type=alert_type,
        target_min_price=data.get("min_price"),
        target_max_price=data.get("max_price"),
        target_price=data.get("target_price"),
        target_drop_percentage=data.get("drop_percentage"),
        reference_price=data.get("reference_price"),
        notify_on_additional_drop=bool(data.get("notify_additional")),
        additional_drop_percentage=data.get("additional_drop"),
    )
    if updated is None:
        await callback.answer("تعذر التعديل", show_alert=True)
        return

    # فحص فوري بالقيم الجديدة: إذا الشرط متحقق الآن أرسل إشعارًا (أول تحقق)
    product = tracking.product
    if product.current_price:
        decision, alert_message = alert_service.evaluate_initial_alert(updated, product, product.current_price)
        if alert_message:
            try:
                await callback.bot.send_message(
                    chat_id=callback.from_user.id,
                    text="🔄 تم تحديث التنبيه — السعر يحقق الشرط الجديد!\n\n" + alert_message,
                    reply_markup=alert_service.build_alert_keyboard(updated),
                )
            except Exception as exc:
                logger.error("Edit alert send failed: %s", exc)

    await state.clear()
    from app.bot.display import product_detail_text
    from app.bot import keyboards as kb2
    refreshed = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    await callback.message.edit_text(
        "✅ <b>تم تحديث التنبيه بنجاح</b>\n\n" + product_detail_text(refreshed),
        reply_markup=kb2.product_card_keyboard(refreshed),
    )
    await callback.answer("تم التعديل")


async def _apply_quick_edit(message: Message, state: FSMContext, mode: str, value: float):
    """تعديل سريع لقيمة واحدة من قائمة المنتجات — دون حذف المنتج."""
    data = await state.get_data()
    tracking_id = data.get("tracking_id")
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, message.from_user.id)
    if tracking is None:
        await message.answer(t.NO_ACCESS)
        await state.clear()
        return

    kwargs = dict(
        alert_type=tracking.alert_type,
        target_min_price=tracking.target_min_price,
        target_max_price=tracking.target_max_price,
        target_price=tracking.target_price,
        target_drop_percentage=tracking.target_drop_percentage,
        reference_price=tracking.reference_price,
        notify_on_additional_drop=tracking.notify_on_additional_drop,
        additional_drop_percentage=tracking.additional_drop_percentage,
    )
    if mode == "min":
        if tracking.target_max_price is not None and value > tracking.target_max_price:
            await message.answer("❌ الحد الأدنى يجب أن يكون أقل من أو يساوي الحد الأعلى الحالي "
                                 f"({format_price(tracking.target_max_price)}). أعد الإدخال:")
            return
        kwargs["target_min_price"] = value
        label = f"الحد الأدنى = {format_price(value)}"
    elif mode == "max":
        if tracking.target_min_price is not None and value < tracking.target_min_price:
            await message.answer("❌ الحد الأعلى يجب أن يكون أكبر من أو يساوي الحد الأدنى الحالي "
                                 f"({format_price(tracking.target_min_price)}). أعد الإدخال:")
            return
        kwargs["target_max_price"] = value
        label = f"الحد الأعلى = {format_price(value)}"
    elif mode == "maxonly":
        kwargs["target_price"] = value
        label = f"السعر الأقصى = {format_price(value)}"
    elif mode == "drop":
        if not (1 <= value <= 99):
            await message.answer(t.INVALID_PERCENTAGE)
            return
        kwargs["target_drop_percentage"] = value
        label = f"نسبة الانخفاض = {value:.0f}%"
    elif mode == "ref":
        kwargs["reference_price"] = value
        label = f"السعر المرجعي = {format_price(value)}"
    else:
        return

    updated = tracking_service.update_tracking_alert(tracking_id, **kwargs)
    await state.clear()

    # فحص فوري بعد التعديل
    if updated and tracking.product.current_price:
        decision, alert_message = alert_service.evaluate_initial_alert(
            updated, tracking.product, tracking.product.current_price
        )
        if alert_message:
            try:
                await message.bot.send_message(
                    chat_id=message.from_user.id,
                    text="🔄 تم تحديث التنبيه — السعر يحقق الشرط الجديد!\n\n" + alert_message,
                    reply_markup=alert_service.build_alert_keyboard(updated),
                )
            except Exception as exc:
                logger.error("Quick edit alert failed: %s", exc)

    from app.bot.display import product_detail_text
    refreshed = tracking_service.get_user_tracking_by_id(tracking_id, message.from_user.id)
    await message.answer(
        f"✅ تم التعديل: {label}\n\n" + product_detail_text(refreshed),
        reply_markup=kb.edit_alert_keyboard(refreshed),
    )


# --------------------------------------------------------------------------
# نقاط دخول التعديل من قائمة منتجاتي
# --------------------------------------------------------------------------

@router.callback_query(F.data.startswith("editalert:"))
async def cb_edit_alert(callback: CallbackQuery, state: FSMContext):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    await state.update_data(tracking_id=tracking_id, product_id=tracking.product_id)
    await callback.message.edit_text(
        "✏️ <b>تعديل نوع التنبيه وقيمه</b>\n\nاختر النوع الجديد (أو نفس النوع لتعديل القيم):",
        reply_markup=kb.alert_type_keyboard(f"edit{tracking_id}"),
    )
    await state.set_state(EditAlertStates.waiting_alert_type)
    await callback.answer()


def _start_quick_edit(callback: CallbackQuery, state: FSMContext, mode: str, prompt: str, edit_state):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        return callback.answer(t.NO_ACCESS, show_alert=True)
    # تعبئة القيم الحالية في السياق ليُبقى التعديل على بقية القيم
    state_data = dict(
        tracking_id=tracking_id,
        product_id=tracking.product_id,
        edit_mode=mode,
        alert_type=tracking.alert_type,
        min_price=tracking.target_min_price,
        max_price=tracking.target_max_price,
        target_price=tracking.target_price,
        drop_percentage=tracking.target_drop_percentage,
        reference_price=tracking.reference_price,
        notify_additional=tracking.notify_on_additional_drop,
        additional_drop=tracking.additional_drop_percentage,
    )
    return _set_quick_state(callback, state, state_data, prompt, edit_state)


async def _set_quick_state(callback: CallbackQuery, state: FSMContext, data: dict, prompt: str, edit_state):
    await state.set_data(data)
    await callback.message.edit_text(prompt, reply_markup=kb.cancel_keyboard())
    await state.set_state(edit_state)
    await callback.answer()


@router.callback_query(F.data.startswith("editmin:"))
async def cb_edit_min(callback: CallbackQuery, state: FSMContext):
    await _start_quick_edit(callback, state, "min", t.RANGE_MIN_PROMPT, EditAlertStates.waiting_min_price)


@router.callback_query(F.data.startswith("editmax:"))
async def cb_edit_max(callback: CallbackQuery, state: FSMContext):
    await _start_quick_edit(callback, state, "max", t.RANGE_MAX_PROMPT.format(min_price="الحالي"), EditAlertStates.waiting_max_price)


@router.callback_query(F.data.startswith("editmaxonly:"))
async def cb_edit_max_only(callback: CallbackQuery, state: FSMContext):
    await _start_quick_edit(callback, state, "maxonly", t.MAX_PRICE_PROMPT, EditAlertStates.waiting_max_price_only)


@router.callback_query(F.data.startswith("editdrop:"))
async def cb_edit_drop(callback: CallbackQuery, state: FSMContext):
    await _start_quick_edit(callback, state, "drop", t.DROP_PERCENTAGE_PROMPT, EditAlertStates.waiting_drop_percentage)


@router.callback_query(F.data.startswith("editref:"))
async def cb_edit_ref(callback: CallbackQuery, state: FSMContext):
    tracking_id = int(callback.data.split(":")[1])
    tracking = tracking_service.get_user_tracking_by_id(tracking_id, callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return
    from app.bot import keyboards as kb2
    await state.clear()
    await state.update_data(
        tracking_id=tracking_id,
        product_id=tracking.product_id,
        edit_mode="ref",
    )
    await callback.message.edit_text(
        t.DROP_REFERENCE_PROMPT,
        reply_markup=kb2.reference_choice_keyboard(f"edit{tracking_id}", tracking.product.current_price or 0),
    )
    await state.set_state(EditAlertStates.waiting_reference_choice)
    await callback.answer()

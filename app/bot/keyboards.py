"""بناء لوحات المفاتيح (Inline Keyboards) للبوت."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database import UserTracking
from app.services.alert_evaluator import AlertType


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ إضافة منتج جديد", callback_data="menu:add")],
        [InlineKeyboardButton(text="📦 منتجاتي المتابعة", callback_data="menu:myproducts")],
        [InlineKeyboardButton(text="📊 سجل الأسعار", callback_data="menu:history")],
        [
            InlineKeyboardButton(text="⚙️ إعداداتي", callback_data="menu:settings"),
            InlineKeyboardButton(text="ℹ️ المساعدة", callback_data="menu:help"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡️ لوحة الأدمن", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alert_type_keyboard(prefix: str = "add") -> InlineKeyboardMarkup:
    """أزرار اختيار نوع التنبيه — prefix يحدد الدفق (add / edit)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 نطاق سعري", callback_data=f"{prefix}:type:{AlertType.PRICE_RANGE.value}")],
        [InlineKeyboardButton(text="🔝 سعر أقصى", callback_data=f"{prefix}:type:{AlertType.MAX_PRICE.value}")],
        [InlineKeyboardButton(text="📉 نسبة انخفاض", callback_data=f"{prefix}:type:{AlertType.PERCENTAGE_DROP.value}")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")],
    ])


def skip_or_cancel_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ تخطي", callback_data=f"{prefix}:skipadditional")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")],
    ])


def reference_choice_keyboard(prefix: str, current_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📌 السعر وقت الإضافة ({current_price:,.0f} جنيه)",
            callback_data=f"{prefix}:ref:addtime",
        )],
        [InlineKeyboardButton(text="✍️ إدخال سعر مرجعي آخر", callback_data=f"{prefix}:ref:custom")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")],
    ])


def confirm_keyboard(prefix: str = "add") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأكيد بدء المتابعة", callback_data=f"{prefix}:confirm")],
        [InlineKeyboardButton(text="✏️ تعديل الأسعار", callback_data=f"{prefix}:backtotype")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")],
    ])


def product_card_keyboard(tracking: UserTracking) -> InlineKeyboardMarkup:
    status_button_text = "▶️ استئناف المتابعة" if not tracking.is_active else "⏸️ إيقاف المتابعة"
    status_callback = f"resumetrack:{tracking.id}" if not tracking.is_active else f"stoptrack:{tracking.id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 تفاصيل المنتج", callback_data=f"detail:{tracking.id}"),
         InlineKeyboardButton(text="📊 سجل الأسعار", callback_data=f"history:{tracking.id}")],
        [InlineKeyboardButton(text="📈 الرسم البياني", callback_data=f"chart:{tracking.id}:30"),
         InlineKeyboardButton(text="✏️ تعديل نوع التنبيه", callback_data=f"editalert:{tracking.id}")],
        [InlineKeyboardButton(text="🔗 فتح المنتج", url=tracking.product.product_url)],
        [InlineKeyboardButton(text=status_button_text, callback_data=status_callback),
         InlineKeyboardButton(text="🗑️ حذف المنتج", callback_data=f"deltrack:{tracking.id}")],
        [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="menu:main")],
    ])


def edit_alert_keyboard(tracking: UserTracking) -> InlineKeyboardMarkup:
    """قائمة التعديل السريع لقيم التنبيه — دون حذف المنتج."""
    buttons = [[InlineKeyboardButton(text="🔄 تعديل نوع التنبيه بالكامل", callback_data=f"editalert:{tracking.id}")]]
    if tracking.alert_type == AlertType.PRICE_RANGE.value:
        buttons.append([InlineKeyboardButton(text="📉 تعديل الحد الأدنى", callback_data=f"editmin:{tracking.id}")])
        buttons.append([InlineKeyboardButton(text="📈 تعديل الحد الأعلى", callback_data=f"editmax:{tracking.id}")])
    elif tracking.alert_type == AlertType.MAX_PRICE.value:
        buttons.append([InlineKeyboardButton(text="🔝 تعديل السعر الأقصى", callback_data=f"editmaxonly:{tracking.id}")])
    elif tracking.alert_type == AlertType.PERCENTAGE_DROP.value:
        buttons.append([InlineKeyboardButton(text="📉 تعديل نسبة الانخفاض", callback_data=f"editdrop:{tracking.id}")])
        buttons.append([InlineKeyboardButton(text="📌 تعديل السعر المرجعي", callback_data=f"editref:{tracking.id}")])
    buttons.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def chart_period_keyboard(tracking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="آخر 7 أيام", callback_data=f"chart:{tracking_id}:7")],
        [InlineKeyboardButton(text="آخر 30 يومًا", callback_data=f"chart:{tracking_id}:30")],
        [InlineKeyboardButton(text="كل التاريخ المتاح", callback_data=f"chart:{tracking_id}:all")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data=f"detail:{tracking_id}")],
    ])


def confirm_delete_keyboard(tracking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ نعم، احذف", callback_data=f"deltrackyes:{tracking_id}")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data=f"detail:{tracking_id}")],
    ])


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="menu:main")],
    ])

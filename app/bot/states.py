"""حالات الحوار (FSM States) للبوت."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddProductStates(StatesGroup):
    """دفق إضافة منتج جديد + تحديد نوع التنبيه."""
    waiting_url = State()              # انتظار رابط المنتج
    waiting_alert_type = State()       # اختيار نوع التنبيه
    waiting_min_price = State()        # نطاق: الحد الأدنى
    waiting_max_price = State()        # نطاق: الحد الأعلى
    waiting_max_price_only = State()   # سعر أقصى: القيمة
    waiting_drop_percentage = State()  # نسبة انخفاض: النسبة
    waiting_reference_choice = State() # نسبة انخفاض: اختيار السعر المرجعي
    waiting_reference_price = State()  # نسبة انخفاض: إدخال سعر مرجعي يدوي
    waiting_additional_drop = State()  # تنبيهات التحسن الإضافي (اختياري)
    confirm_summary = State()          # تأكيد الملخص


class EditAlertStates(StatesGroup):
    """دفق تعديل التنبيه من قائمة منتجاتي (دون حذف المنتج)."""
    waiting_alert_type = State()
    waiting_min_price = State()
    waiting_max_price = State()
    waiting_max_price_only = State()
    waiting_drop_percentage = State()
    waiting_reference_choice = State()
    waiting_reference_price = State()
    waiting_additional_drop = State()
    confirm_summary = State()

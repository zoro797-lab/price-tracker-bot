"""اختبار تكامل عبر الـ dispatcher الحقيقي: تدفق إضافة منتج كامل خطوة بخطوة.

هذا الاختبار يلتقط عيوب التوجيه التي لا يراها اختبارات الوحدة
(مثل فلاتر الحالات المجموعة بـ AND المستحيل — وحدث فعلًا في الإنتاج).

خطوات الرسائل تمر عبر dispatcher.feed_update (توجيه حقيقي)،
وخطوات الأزرار تستدعي المعالجات مباشرة (توجيه الأزرار كان سليمًا
وفقط رسائل pydantic لا تمر عبر feed_update ككائنات مزيفة).
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, User, Update

from app.providers.base import AvailabilityStatus, ProductSnapshot


class FakeSent:
    """رسالة مزيفة قابلة للتعديل — تحاكي رد البوت."""

    def __init__(self, text: str = ""):
        self.text = text

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.text = text
        return self

    async def answer(self, text=None, show_alert=None, **kwargs):
        return None


class FakeBot:
    def __init__(self):
        self.sent: list[FakeSent] = []

    async def __call__(self, method):
        """يدعم message.answer/edit_* — تسجل الرسائل وتعيد كائنًا قابلًا للتعديل."""
        text = getattr(method, "text", None)
        if text is None:
            text = getattr(method, "message", "") or ""
        m = FakeSent(text if isinstance(text, str) else str(text))
        self.sent.append(m)
        return m

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        m = FakeSent(text)
        self.sent.append(m)
        return m

    @property
    def id(self) -> int:
        return 42


class FakeCallback:
    """callback_query مزيف لاستدعاء المعالجات مباشرة."""

    def __init__(self, data: str, message: FakeSent, bot: FakeBot):
        self.data = data
        self.message = message
        self.bot = bot
        self.from_user = User(id=336073669, is_bot=False, first_name="Mahmoud")

    async def answer(self, text=None, show_alert=False, **kwargs):
        return None


def _make_message(text: str, message_id: int = 1) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(),
        chat=Chat(id=336073669, type="private"),
        from_user=User(id=336073669, is_bot=False, first_name="Mahmoud", username="MahmoudAdawy20"),
        text=text,
    )


def _is_unhandled(result) -> bool:
    return str(result) == "sentinel.UNHANDLED"


@pytest.fixture()
def mock_provider(monkeypatch):
    """مزود وهمي سريع بدل الطلبات الشبكية — بيانات وهمية للاختبار فقط."""
    import app.providers.registry as reg

    class QuickProvider:
        store_key = "amazon"
        store_display_name = "Amazon.eg"
        url_domains = ("amazon.eg",)

        def matches_url(self, url):
            return True

        def extract_product_id(self, url):
            return "B0TESTTEST"

        def fetch_product(self, url):
            return ProductSnapshot(
                store="amazon",
                title="منتج تدفق كامل",
                price=500.0,
                availability=AvailabilityStatus.AVAILABLE,
                external_id=f"FSM{time.time_ns() % 10**8}",
                data_source="mock",
            )

    provider = QuickProvider()
    monkeypatch.setattr(reg, "PROVIDERS", [provider])
    monkeypatch.setattr(reg, "PROVIDERS_BY_KEY", {"amazon": provider})
    return provider


@pytest.mark.asyncio()
async def test_full_price_range_flow_through_dispatcher(mock_provider, dispatcher):
    """التدفق الكامل: رابط → نوع نطاق → 385 → 450 → تخطي التحسن → تأكيد → متابعة مسجلة."""
    from app.bot.handlers_add import cb_alert_type_chosen, cb_confirm, cb_skip_additional
    from app.services import tracking_service

    bot = FakeBot()
    url = "https://www.amazon.eg/-/ar/dp/B0B1BY4671/?smid=X"

    # حالة FSM مربوطة بنفس تخزين الـ dispatcher
    state = FSMContext(
        dispatcher.storage,
        StorageKey(bot_id=bot.id, chat_id=336073669, user_id=336073669),
    )

    # 1) إرسال الرابط — عبر التوجيه الحقيقي
    r = await dispatcher.feed_update(bot=bot, update=Update(update_id=1, message=_make_message(url, 1)))
    assert not _is_unhandled(r), "رسالة الرابط وصلت unhandled!"
    card = bot.sent[-1]
    assert "منتج تدفق كامل" in card.text
    assert "500" in card.text  # السعر الحالي ظاهر في البطاقة

    # 2) اختيار نوع التنبيه: نطاق سعري (معالج أزرار مباشرة)
    card = FakeSent(card.text)
    await cb_alert_type_chosen(FakeCallback("add:type:price_range", card, bot), state)
    assert "الخطوة 1 من 2" in card.text, "لم تظهر خطوة الحد الأدنى!"

    # 3) إدخال الحد الأدنى — هذه الخطوة كانت معطلة في الإنتاج (عيب فلاتر الحالات)!
    r = await dispatcher.feed_update(bot=bot, update=Update(update_id=3, message=_make_message("385", 2)))
    assert not _is_unhandled(r), "إدخال الحد الأدنى unhandled!"
    assert "الخطوة 2 من 2" in bot.sent[-1].text, "لم تظهر خطوة الحد الأعلى!"

    # 4) إدخال حد أعلى أصغر من الأدنى → يجب أن يُرفض ويُعاد السؤال
    r = await dispatcher.feed_update(bot=bot, update=Update(update_id=4, message=_make_message("300", 3)))
    assert not _is_unhandled(r), "التحقق من الحد الأعلى unhandled!"
    assert "أقل" in bot.sent[-1].text or "خطأ" in bot.sent[-1].text, "لم يرفض الحد الأعلى الأصغر!"

    # 5) إدخال حد أعلى صحيح → سؤال التحسن الإضافي
    r = await dispatcher.feed_update(bot=bot, update=Update(update_id=5, message=_make_message("450", 4)))
    assert not _is_unhandled(r), "إدخال الحد الأعلى الصحيح unhandled!"
    assert "اختياري" in bot.sent[-1].text

    # 6) تخطي تنبيه التحسن الإضافي → الملخص
    card = FakeSent(bot.sent[-1].text)
    await cb_skip_additional(FakeCallback("add:skipadditional", card, bot), state)
    assert "ملخص المتابعة" in card.text
    assert "385" in card.text and "450" in card.text, "القيم غير ظاهرة في الملخص"

    # 7) التأكيد النهائي → تسجيل المتابعة + إشعار الأدمن
    sent_before = len(bot.sent)
    await cb_confirm(FakeCallback("add:confirm", card, bot), state)
    new_messages = bot.sent[sent_before:]
    assert any("متابعة" in m.text for m in new_messages), "لا رسالة نجاح للمستخدم"
    all_text = " ".join(m.text for m in new_messages)
    assert "نطاق سعري" in all_text, "إشعار الأدمن لا يذكر نوع التنبيه"
    assert "385" in all_text and "450" in all_text, "إشعار الأدمن لا يذكر النطاق"

    # 8) المتابعة مسجلة فعلًا في قاعدة البيانات بالقيم الصحيحة
    trackings = tracking_service.get_user_trackings(336073669)
    matching = [
        tr for tr in trackings
        if tr.target_min_price == 385 and tr.target_max_price == 450
    ]
    assert matching, "المتابعة لم تُسجل في قاعدة البيانات!"
    tracking = matching[0]
    assert tracking.alert_type == "price_range"
    assert tracking.is_active is True
    assert tracking.alert_armed is True  # مسلح بانتظار أول فحص يجعل السعر ≤ 450

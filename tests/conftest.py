"""إعدادات مشتركة للاختبارات — قاعدة بيانات مؤقتة لكل جلسة اختبار."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# السماح باستيراد app من مجلد المشروع
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.database.database import init_db, db_session  # noqa: E402
from app.database.models import User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def test_database(tmp_path_factory):
    """تهيئة قاعدة بيانات SQLite مؤقتة لكل جلسة اختبار."""
    db_path = tmp_path_factory.mktemp("data") / "test_price_tracker.db"
    init_db(db_path)
    yield db_path


@pytest.fixture()
def sample_user() -> User:
    """مستخدم اختبار جديد (معرف فريد لكل اختبار)."""
    import time
    telegram_id = int(f"9{time.time_ns() % 10**10:010d}")
    from app.services.user_service import get_or_create_user
    user, _ = get_or_create_user(telegram_id, "Test", "User", username=f"test_{telegram_id}")
    return user


class FakeTracking:
    """كائن خفيف يحاكي UserTracking لاختبار المنطق النقي بدون قاعدة بيانات."""

    def __init__(self, **kwargs):
        self.alert_type = "price_range"
        self.target_min_price = None
        self.target_max_price = None
        self.target_price = None
        self.target_drop_percentage = None
        self.reference_price = None
        self.alert_armed = True
        self.notify_on_additional_drop = False
        self.additional_drop_percentage = None
        self.last_alerted_price = None
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture()
def fake_tracking():
    return FakeTracking


class FakeBot:
    """بوت وهمي يسجل الرسائل المرسلة — لاختبار الإرسال بدون Telegram."""

    def __init__(self):
        self.sent_messages = []

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return True


@pytest.fixture()
def fake_bot():
    return FakeBot()

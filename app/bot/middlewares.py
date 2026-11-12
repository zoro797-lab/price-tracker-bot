"""Middleware: تسجيل المستخدم تلقائيًا ومنع المحظورين."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.config import settings
from app.services.user_service import get_or_create_user, is_user_blocked
from app.utils.logger import get_logger

logger = get_logger("middleware")


class UserContextMiddleware(BaseMiddleware):
    """يضيف user_record (قاعدة بيانات) و is_admin لكل تحديث، ويسجل المستخدمين الجدد."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # استخراج معرف المرسل من أي نوع تحديث
        telegram_id = None
        first_name = ""
        last_name = ""
        username = None

        message = data.get("event_message") or getattr(event, "message", None)
        callback_query = getattr(event, "callback_query", None)
        source = message or callback_query
        if source is not None:
            user = getattr(source, "from_user", None)
            if user is not None:
                telegram_id = user.id
                first_name = user.first_name or ""
                last_name = user.last_name or ""
                username = user.username
        else:
            my_chat_member = getattr(event, "my_chat_member", None)
            if my_chat_member is not None and my_chat_member.from_user:
                telegram_id = my_chat_member.from_user.id

        if telegram_id is None:
            return await handler(event, data)

        # منع المحظورين تمامًا
        if is_user_blocked(telegram_id):
            logger.debug("Blocked user %s ignored", telegram_id)
            return None

        user_record, is_new = get_or_create_user(telegram_id, first_name, last_name, username)
        if is_new:
            logger.info("New user registered: %s (%s)", telegram_id, username)

        data["user_record"] = user_record
        data["is_new_user"] = is_new
        data["is_admin"] = telegram_id in settings.admin_id_list
        return await handler(event, data)

"""خدمة الإشعارات — تسجيل قبل الإرسال (ضمان عدم ضياعها) ثم تحديث الحالة."""
from __future__ import annotations

from app.database import Notification, User, db_session
from app.utils.formatting import localize, now
from app.utils.logger import get_logger

logger = get_logger("notifications")


def queue_notification(
    user_id: int,
    product_id: int | None,
    tracking_id: int | None,
    notification_type: str,
    price_at_notification: float | None,
    message: str,
) -> int:
    """إنشاء إشعار بحالة pending **قبل** الإرسال — يضمن عدم ضياع التنبيه
    عند إعادة تشغيل البرنامج أثناء الإرسال (يُعاد محاولة الإرسال عند الإقلاع)."""
    with db_session() as session:
        notification = Notification(
            user_id=user_id,
            product_id=product_id,
            tracking_id=tracking_id,
            notification_type=notification_type,
            price_at_notification=price_at_notification,
            message=message,
            status="pending",
            created_at=localize(now()),
        )
        session.add(notification)
        session.flush()
        return notification.id


def mark_sent(notification_id: int) -> None:
    with db_session() as session:
        (
            session.query(Notification)
            .filter(Notification.id == notification_id)
            .update({"status": "sent", "sent_at": localize(now())}, synchronize_session=False)
        )


def mark_failed(notification_id: int) -> None:
    with db_session() as session:
        (
            session.query(Notification)
            .filter(Notification.id == notification_id)
            .update({"status": "failed", "sent_at": localize(now())}, synchronize_session=False)
        )


async def send_pending_notifications(bot, max_batch: int = 100) -> int:
    """إعادة إرسال الإشعارات المعلقة/الفاشلة (تُستدعى عند الإقلاع ودوريًا).

    يعيد عدد الإشعارات التي أُرسلت بنجاح.
    """
    with db_session() as session:
        pending = (
            session.query(Notification)
            .filter(Notification.status.in_(("pending", "failed")))
            .order_by(Notification.created_at.asc())
            .limit(max_batch)
            .all()
        )
        items = [(n.id, n.user_id, n.message) for n in pending]

    sent = 0
    for notification_id, user_id, message in items:
        try:
            with db_session() as session:
                user = session.query(User).filter(User.id == user_id).first()
                if not user or user.is_blocked:
                    mark_failed(notification_id)
                    continue
                telegram_id = user.telegram_id
            await bot.send_message(chat_id=telegram_id, text=message)
            mark_sent(notification_id)
            sent += 1
        except Exception as exc:
            logger.warning("Pending notification %s failed: %s", notification_id, exc)
            mark_failed(notification_id)
    return sent


def recent_notifications(limit: int = 20, user_id: int | None = None) -> list[Notification]:
    with db_session() as session:
        query = session.query(Notification).order_by(Notification.created_at.desc())
        if user_id is not None:
            query = query.filter(Notification.user_id == user_id)
        return query.limit(limit).all()


def count_notifications() -> dict:
    with db_session() as session:
        total = session.query(Notification).count()
        sent = session.query(Notification).filter(Notification.status == "sent").count()
        pending = session.query(Notification).filter(Notification.status == "pending").count()
        failed = session.query(Notification).filter(Notification.status == "failed").count()
        return {"total": total, "sent": sent, "pending": pending, "failed": failed}

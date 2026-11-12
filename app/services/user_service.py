"""خدمة المستخدمين: تسجيل تلقائي، حظر، استعلامات."""
from __future__ import annotations

from app.database import User, db_session
from app.utils.formatting import localize, now


def get_or_create_user(
    telegram_id: int,
    first_name: str = "",
    last_name: str = "",
    username: str | None = None,
) -> tuple[User, bool]:
    """إرجاع (المستخدم، هل هو جديد؟) — يسجل المستخدم الجديد تلقائيًا."""
    with db_session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        if user:
            # تحديث الاسم/اليوزر إن تغيّر
            changed = False
            if first_name and user.first_name != first_name:
                user.first_name, changed = first_name, True
            if last_name and user.last_name != last_name:
                user.last_name, changed = last_name, True
            if (username or None) != user.username:
                user.username, changed = username or None, True
            if changed:
                user.created_at = user.created_at  # لا نغير تاريخ التسجيل
            return user, False
        user = User(
            telegram_id=telegram_id,
            first_name=first_name or "",
            last_name=last_name or "",
            username=username,
            is_active=True,
            is_blocked=False,
            created_at=localize(now()),
        )
        session.add(user)
        session.flush()
        return user, True


def get_user_by_telegram_id(telegram_id: int) -> User | None:
    with db_session() as session:
        return session.query(User).filter(User.telegram_id == telegram_id).first()


def is_user_blocked(telegram_id: int) -> bool:
    user = get_user_by_telegram_id(telegram_id)
    return bool(user and user.is_blocked)


def set_blocked(user_id: int, blocked: bool) -> bool:
    with db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        user.is_blocked = blocked
        user.is_active = not blocked
    return True


def delete_user(user_id: int) -> bool:
    with db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        session.delete(user)
    return True


def count_users() -> dict:
    with db_session() as session:
        total = session.query(User).count()
        blocked = session.query(User).filter(User.is_blocked.is_(True)).count()
        return {"total": total, "active": total - blocked, "blocked": blocked}


def list_users(limit: int = 50) -> list[User]:
    with db_session() as session:
        return (
            session.query(User)
            .order_by(User.created_at.desc())
            .limit(limit)
            .all()
        )


def find_users(query: str) -> list[User]:
    """بحث بـ Telegram ID أو Username."""
    with db_session() as session:
        q = query.strip().lstrip("@").lower()
        base = session.query(User)
        if q.isdigit():
            return base.filter(User.telegram_id == int(q)).all()
        return base.filter(User.username.isnot(None)).all()

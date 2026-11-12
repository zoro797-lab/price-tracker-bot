"""خدمة المتابعات: إنشاء/تعديل التنبيه، الحالة الذرية لمنع تكرار الإشعارات."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import joinedload

from app.database import Product, User, UserTracking, db_session
from app.services.alert_evaluator import AlertDecision, evaluate_alert
from app.utils.formatting import localize, now
from app.utils.logger import get_logger

logger = get_logger("tracking")


# --------------------------------------------------------------------------
# إنشاء وتعديل
# --------------------------------------------------------------------------

def create_tracking(
    user: User,
    product: Product,
    *,
    alert_type: str,
    target_min_price: float | None = None,
    target_max_price: float | None = None,
    target_price: float | None = None,
    target_drop_percentage: float | None = None,
    reference_price: float | None = None,
    notify_on_additional_drop: bool = False,
    additional_drop_percentage: float | None = None,
) -> UserTracking:
    """إنشاء متابعة جديدة — التنبيه مسلح افتراضيًا لأول فحص."""
    with db_session() as session:
        tracking = UserTracking(
            user_id=user.id,
            product_id=product.id,
            alert_type=alert_type,
            target_min_price=target_min_price,
            target_max_price=target_max_price,
            target_price=target_price,
            target_drop_percentage=target_drop_percentage,
            reference_price=reference_price,
            is_active=True,
            alert_armed=True,
            notify_on_additional_drop=notify_on_additional_drop,
            additional_drop_percentage=additional_drop_percentage,
            created_at=localize(now()),
            updated_at=localize(now()),
        )
        session.add(tracking)
        session.flush()
        session.refresh(tracking)
        return tracking


def update_tracking_alert(
    tracking_id: int,
    *,
    alert_type: str,
    target_min_price: float | None = None,
    target_max_price: float | None = None,
    target_price: float | None = None,
    target_drop_percentage: float | None = None,
    reference_price: float | None = None,
    notify_on_additional_drop: bool = False,
    additional_drop_percentage: float | None = None,
) -> UserTracking | None:
    """تعديل نوع التنبيه وقيمه دون حذف المنتج — وإعادة ضبط حالة التنبيه بشكل صحيح:

    - تصفير آخر إشعار (سعر/وقت).
    - إعادة تسليح التنبيه (alert_armed=True) ليُرسل إشعارًا عند أول فحص
      يحقق الشرط بالقيم الجديدة.
    """
    with db_session() as session:
        tracking = session.query(UserTracking).filter(UserTracking.id == tracking_id).first()
        if not tracking:
            return None
        tracking.alert_type = alert_type
        tracking.target_min_price = target_min_price
        tracking.target_max_price = target_max_price
        tracking.target_price = target_price
        tracking.target_drop_percentage = target_drop_percentage
        tracking.reference_price = reference_price
        tracking.notify_on_additional_drop = notify_on_additional_drop
        tracking.additional_drop_percentage = additional_drop_percentage
        # إعادة ضبط حالة التنبيه
        tracking.alert_armed = True
        tracking.last_alerted_price = None
        tracking.last_alerted_at = None
        tracking.updated_at = localize(now())
        session.flush()
        session.refresh(tracking)
        return tracking


def set_tracking_active(tracking_id: int, active: bool) -> bool:
    """إيقاف/استئناف المتابعة. عند الاستئناف يُعاد تسليح التنبيه."""
    with db_session() as session:
        tracking = session.query(UserTracking).filter(UserTracking.id == tracking_id).first()
        if not tracking:
            return False
        tracking.is_active = active
        if active:
            tracking.alert_armed = True
            tracking.last_alerted_price = None
            tracking.last_alerted_at = None
        tracking.updated_at = localize(now())
    return True


def delete_tracking(tracking_id: int) -> bool:
    with db_session() as session:
        tracking = session.query(UserTracking).filter(UserTracking.id == tracking_id).first()
        if not tracking:
            return False
        session.delete(tracking)
    return True


def clear_alert_state(tracking_id: int) -> bool:
    """تصفير حالة التنبيه يدويًا (يُستخدم بعد تعديل القيم)."""
    with db_session() as session:
        tracking = session.query(UserTracking).filter(UserTracking.id == tracking_id).first()
        if not tracking:
            return False
        tracking.alert_armed = True
        tracking.last_alerted_price = None
        tracking.last_alerted_at = None
    return True


# --------------------------------------------------------------------------
# استعلامات
# --------------------------------------------------------------------------

def get_tracking(tracking_id: int) -> UserTracking | None:
    with db_session() as session:
        return (
            session.query(UserTracking)
            .options(joinedload(UserTracking.product))
            .filter(UserTracking.id == tracking_id)
            .first()
        )


def get_user_trackings(telegram_id: int, include_inactive: bool = True) -> list[UserTracking]:
    with db_session() as session:
        query = (
            session.query(UserTracking)
            .join(User, UserTracking.user_id == User.id)
            .options(joinedload(UserTracking.product))
            .filter(User.telegram_id == telegram_id)
        )
        if not include_inactive:
            query = query.filter(UserTracking.is_active.is_(True))
        return query.order_by(UserTracking.created_at.desc()).all()


def get_user_tracking_by_id(tracking_id: int, telegram_id: int) -> UserTracking | None:
    """جلب متابعة مع التحقق من ملكية المستخدم (حماية من الوصول لبيانات غيره)."""
    with db_session() as session:
        return (
            session.query(UserTracking)
            .join(User, UserTracking.user_id == User.id)
            .options(joinedload(UserTracking.product))
            .filter(UserTracking.id == tracking_id, User.telegram_id == telegram_id)
            .first()
        )


def count_user_trackings(telegram_id: int) -> int:
    with db_session() as session:
        return (
            session.query(UserTracking)
            .join(User, UserTracking.user_id == User.id)
            .filter(User.telegram_id == telegram_id, UserTracking.is_active.is_(True))
            .count()
        )


def count_all_trackings(active_only: bool = True) -> int:
    with db_session() as session:
        query = session.query(UserTracking)
        if active_only:
            query = query.filter(UserTracking.is_active.is_(True))
        return query.count()


# --------------------------------------------------------------------------
# الحالة الذرية لمنع تكرار الإشعارات
# --------------------------------------------------------------------------

def claim_alert(tracking_id: int, decision: AlertDecision, price: float,
                expected_armed: bool, expected_last_price: float | None) -> bool:
    """حجز الحق في إرسال إشعار — **بشرط SQL ذري** يمنع الإشعارات المكررة
    الناتجة عن تشغيل أكثر من عملية فحص في نفس الوقت.

    - إشعار جديد ("new"): يشترط أن يكون alert_armed بالقيمة المتوقعة (True).
    - إشعار تحسن إضافي ("improved"): يشترط أن last_alerted_price لم يتغير
      منذ القراءة (حماية من سباق فحصين متوازيين).
    عند النجاح يسجل السعر ووقت آخر إشعار ويعيد True.
    """
    ts = localize(now())
    values = {
        "last_alerted_price": price,
        "last_alerted_at": ts,
        "updated_at": ts,
    }
    with db_session() as session:
        if decision.alert_kind == "new":
            values["alert_armed"] = False
            condition = (
                UserTracking.id == tracking_id,
                UserTracking.alert_armed.is_(expected_armed),
            )
        else:  # improved
            condition = (
                UserTracking.id == tracking_id,
                UserTracking.alert_armed.is_(False),
            )
            if expected_last_price is None:
                condition = (*condition, UserTracking.last_alerted_price.is_(None))
            else:
                condition = (*condition, UserTracking.last_alerted_price == expected_last_price)

        updated = (
            session.query(UserTracking)
            .filter(*condition)
            .update(values, synchronize_session=False)
        )
        return updated > 0


def apply_rearm_atomically(tracking_id: int) -> None:
    """إعادة تسليح التنبيه (السعر ارتفع فوق الحد) — عملية ذرية غير ضارة."""
    with db_session() as session:
        session.query(UserTracking).filter(
            UserTracking.id == tracking_id,
            UserTracking.alert_armed.is_(False),
        ).update({"alert_armed": True, "updated_at": localize(now())}, synchronize_session=False)

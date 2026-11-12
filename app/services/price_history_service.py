"""خدمة سجل الأسعار التاريخي — حفظ القراءات الحقيقية فقط وحساب الإحصائيات."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func

from app.database import PriceHistory, Product, db_session
from app.utils.formatting import localize, now


def record_price(
    product_id: int,
    price: float,
    currency: str = "EGP",
    data_source: str | None = None,
    availability_status: str = "unknown",
    min_gap_minutes: int = 5,
) -> bool:
    """تسجيل قراءة سعر — القراءات الفاشلة لا تُسجل أبدًا (يستدعى فقط بعد نجاح).

    يمنع التسجيل المكرر غير الضروري: قراءة بنفس السعر خلال فترة أقل من
    `min_gap_minutes` لا تُضاف مجددًا (حماية داخل نفس دورة الفحص).
    """
    with db_session() as session:
        cutoff = localize(now()) - timedelta(minutes=min_gap_minutes)
        duplicate = (
            session.query(PriceHistory)
            .filter(
                PriceHistory.product_id == product_id,
                PriceHistory.price == price,
                PriceHistory.recorded_at >= cutoff,
            )
            .first()
        )
        if duplicate:
            return False
        session.add(
            PriceHistory(
                product_id=product_id,
                price=price,
                currency=currency,
                recorded_at=localize(now()),
                data_source=data_source,
                availability_status=availability_status,
            )
        )
        return True


@dataclass
class PriceStats:
    days_count: int          # عدد الأيام التي توجد بها قراءات فعليًا
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None
    current_price: float | None
    min_price: float | None
    max_price: float | None
    avg_price: float | None
    change_percentage: float | None   # نسبة التغير من أول قراءة إلى الحالية
    readings_count: int


def get_history(product_id: int, days: int | None = None) -> list[PriceHistory]:
    """قراءات المنتج — days=None يعني كل التاريخ المتاح."""
    with db_session() as session:
        query = (
            session.query(PriceHistory)
            .filter(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.recorded_at.asc())
        )
        if days:
            cutoff = localize(now()) - timedelta(days=days)
            query = query.filter(PriceHistory.recorded_at >= cutoff)
        return query.all()


def get_stats(product_id: int, days: int = 30) -> PriceStats:
    """إحصائيات الفترة المطلوبة — تُحسب من البيانات المتوفرة فعلًا فقط.

    مهم: إذا كان المنتج مضافًا منذ 5 أيام فقط، فإن days_count=5
    ولا تُدّعى بيانات 30 يومًا أبدًا.
    """
    history = get_history(product_id, days=days)
    prices = [h.price for h in history]

    if not prices:
        return PriceStats(0, None, None, None, None, None, None, None, 0)

    first_price, last_price = prices[0], prices[-1]
    change_pct = (
        round((last_price - first_price) / first_price * 100, 2)
        if first_price > 0 else None
    )
    # عدد الأيام الفعلية التي بها قراءات
    unique_days = {h.recorded_at.date() for h in history}

    return PriceStats(
        days_count=len(unique_days),
        first_recorded_at=history[0].recorded_at,
        last_recorded_at=history[-1].recorded_at,
        current_price=last_price,
        min_price=min(prices),
        max_price=max(prices),
        avg_price=round(sum(prices) / len(prices), 2),
        change_percentage=change_pct,
        readings_count=len(prices),
    )


def get_recent_readings(product_id: int, limit: int = 10) -> list[PriceHistory]:
    with db_session() as session:
        return (
            session.query(PriceHistory)
            .filter(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.recorded_at.desc())
            .limit(limit)
            .all()
        )


def cleanup_old_history(retention_days: int) -> int:
    """حذف القراءات الأقدم من فترة الاحتفاظ — يعيد عدد المحذوف."""
    with db_session() as session:
        cutoff = localize(now()) - timedelta(days=retention_days)
        deleted = (
            session.query(PriceHistory)
            .filter(PriceHistory.recorded_at < cutoff)
            .delete(synchronize_session=False)
        )
    return deleted


def count_products() -> dict:
    with db_session() as session:
        total = session.query(Product).count()
        amazon = session.query(Product).filter(Product.store == "amazon").count()
        noon = session.query(Product).filter(Product.store == "noon").count()
        return {"total": total, "amazon": amazon, "noon": noon}

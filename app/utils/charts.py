"""إنشاء الرسوم البيانية لسجل الأسعار — مع دعم عرض النطاق المستهدف."""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # بدون واجهة رسومية (Windows server-safe)
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from app.database import UserTracking
from app.services.alert_evaluator import AlertType
from app.services.price_history_service import get_history, get_stats
from app.utils.formatting import get_tz

try:
    import arabic_reshaper
    from bidi.algorithm import get_display

    def _ar(text: str) -> str:
        """تهيئة النص العربي للعرض الصحيح في Matplotlib."""
        return get_display(arabic_reshaper.reshape(text))
except ImportError:  # احتياط: تظهر العربية غير متصلة لكن الرسم يعمل
    def _ar(text: str) -> str:
        return text


def build_price_chart(tracking: UserTracking, days: int | None = 30) -> Path | None:
    """إنشاء رسم بياني لسجل الأسعار. days=None يعني كل التاريخ المتاح.

    يعيد مسار ملف PNG أو None إذا لا توجد بيانات كافية.
    """
    history = get_history(tracking.product_id, days=days)
    if len(history) < 2:
        return None

    dates = [h.recorded_at.replace(tzinfo=get_tz()) for h in history]
    prices = [h.price for h in history]

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=130)
    fig.patch.set_facecolor("#ffffff")

    ax.plot(dates, prices, color="#1f77b4", linewidth=2, marker="o", markersize=4, label=_ar("السعر"))

    # النطاق المستهدف — إبراز المنطقة بوضوح
    if tracking.alert_type == AlertType.PRICE_RANGE.value and tracking.target_min_price and tracking.target_max_price:
        ax.axhspan(
            tracking.target_min_price,
            tracking.target_max_price,
            color="#2ecc71", alpha=0.18,
            label=_ar("النطاق المستهدف"),
        )
        ax.axhline(tracking.target_max_price, color="#27ae60", linestyle="--", linewidth=1.4,
                   label=_ar("الحد الأعلى"))
        ax.axhline(tracking.target_min_price, color="#95a5a6", linestyle=":", linewidth=1.2,
                   label=_ar("الحد الأدنى"))
    elif tracking.alert_type == AlertType.MAX_PRICE.value and tracking.target_price:
        ax.axhline(tracking.target_price, color="#e67e22", linestyle="--", linewidth=1.6,
                   label=_ar("السعر الأقصى المستهدف"))
    elif tracking.alert_type == AlertType.PERCENTAGE_DROP.value and tracking.reference_price and tracking.target_drop_percentage:
        threshold = tracking.reference_price * (1 - tracking.target_drop_percentage / 100)
        ax.axhline(tracking.reference_price, color="#95a5a6", linestyle=":", linewidth=1.2,
                   label=_ar("السعر المرجعي"))
        ax.axhline(threshold, color="#e74c3c", linestyle="--", linewidth=1.6,
                   label=_ar("عتبة التنبيه"))

    # أعلى/أقل سعر
    ax.axhline(max(prices), color="#f39c12", linestyle=":", linewidth=1, alpha=0.8)
    ax.axhline(min(prices), color="#3498db", linestyle=":", linewidth=1, alpha=0.8)

    stats = get_stats(tracking.product_id, days=days or 36500)
    title_days = _ar(f"آخر {days} يومًا") if days else _ar("كل التاريخ المتاح")
    ax.set_title(
        _ar(f"سجل الأسعار — {stats.days_count} يومًا متاح ({title_days})"),
        fontsize=13,
    )
    ax.set_xlabel(_ar("التاريخ"), fontsize=11)
    ax.set_ylabel(_ar("السعر (جنيه)"), fontsize=11)
    ax.legend(loc="best", fontsize=9, prop=None)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=get_tz()))
    fig.autofmt_xdate()
    fig.tight_layout()

    # حفظ في ملف مؤقت
    tmp = Path(tempfile.gettempdir()) / f"chart_{tracking.id}_{days or 'all'}.png"
    fig.savefig(tmp, format="png")
    plt.close(fig)
    return tmp

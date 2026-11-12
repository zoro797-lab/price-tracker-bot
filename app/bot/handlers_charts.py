"""معالجات الرسم البياني — إرسال صورة Matplotlib عبر Telegram."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile

from app.bot import keyboards as kb
from app.bot import texts as t
from app.services import tracking_service
from app.utils.charts import build_price_chart
from app.utils.logger import get_logger

logger = get_logger("chart_handlers")
router = Router()


@router.callback_query(F.data.regexp(r"^chart:(\d+):(7|30|all)$"))
async def cb_chart(callback: CallbackQuery):
    _, tracking_id, period = callback.data.split(":")
    tracking = tracking_service.get_user_tracking_by_id(int(tracking_id), callback.from_user.id)
    if tracking is None:
        await callback.answer(t.NO_ACCESS, show_alert=True)
        return

    days: int | None = None if period == "all" else int(period)
    await callback.answer("⏳ جاري إنشاء الرسم البياني...")

    try:
        chart_path = build_price_chart(tracking, days=days)
    except Exception as exc:
        logger.error("Chart build failed: %s", exc)
        await callback.message.answer("❌ تعذر إنشاء الرسم البياني، جرّب لاحقًا.")
        return

    if chart_path is None:
        await callback.message.answer(t.CHART_NO_DATA, reply_markup=kb.chart_period_keyboard(tracking.id))
        return

    try:
        await callback.message.answer_photo(
            photo=FSInputFile(chart_path),
            caption=f"📈 الرسم البياني لسعر: {tracking.product.title[:60]}",
        )
    finally:
        try:
            chart_path.unlink(missing_ok=True)
        except Exception:
            pass

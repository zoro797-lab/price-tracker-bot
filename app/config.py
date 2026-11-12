"""إعدادات المشروع — تُقرأ من ملف .env وتتحقق منها Pydantic."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """كل الإعدادات تأتي من متغيرات البيئة أو القيم الافتراضية."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids: str = Field(default="", alias="ADMIN_IDS")

    # --- Webhook (للنشر على Render أو أي استضافة) ---
    use_webhook: bool = Field(default=False, alias="USE_WEBHOOK")
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")

    # --- Database ---
    db_path: str = Field(default=str(BASE_DIR / "data" / "price_tracker.db"), alias="DB_PATH")

    # --- Scheduler ---
    check_interval_hours: int = Field(default=6, alias="CHECK_INTERVAL_HOURS")

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Timezone ---
    timezone: str = Field(default="Africa/Cairo", alias="TIMEZONE")

    # --- Network ---
    request_timeout_seconds: int = Field(default=20, alias="REQUEST_TIMEOUT_SECONDS")
    request_retries: int = Field(default=2, alias="REQUEST_RETRIES")

    @field_validator("bot_token")
    @classmethod
    def _warn_token(cls, v: str) -> str:
        return v.strip()

    @property
    def admin_id_list(self) -> list[int]:
        """قائمة معرفات الأدمن (ADMIN_IDS=123,456)."""
        result = []
        for part in self.admin_ids.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
        return result

    @property
    def external_url(self) -> str:
        """الرابط الخارجي للخدمة — يأتي من WEBHOOK_BASE_URL أو من Render تلقائيًا."""
        import os

        return (
            self.webhook_base_url.rstrip("/")
            or os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        )

    @property
    def webhook_url(self) -> str:
        """الرابط الكامل للـ webhook (يُسجل عند Telegram)."""
        return f"{self.external_url}/webhook"

    @property
    def effective_webhook_secret(self) -> str:
        """سر الـ webhook — إن لم يُحدد نستخدم بصمة ثابتة من التوكن."""
        import hashlib

        if self.webhook_secret:
            return self.webhook_secret
        return hashlib.sha256(self.bot_token.encode()).hexdigest()[:32]

    def should_use_webhook(self) -> bool:
        """وضع webhook مفعل صراحة أو تشتغل داخل Render."""
        import os

        return self.use_webhook or bool(os.environ.get("RENDER_EXTERNAL_URL"))

    @property
    def database_path(self) -> Path:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()

"""Configuration and settings for the MEXC Alert Bot."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    telegram_bot_token: str
    timezone: str
    alert_tolerance_percent: float
    price_poll_interval_seconds: int
    mexc_api_base: str
    alerts_file: Path

    # Derived
    @property
    def alerts_file_path(self) -> Path:
        return self.alerts_file


def load_settings() -> Settings:
    """Load settings from environment. Raises if required values are missing."""
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is required. "
            "Copy .env.example to .env and set your bot token from @BotFather."
        )

    tz = os.getenv("TIMEZONE", "Asia/Singapore")
    tolerance = float(os.getenv("ALERT_TOLERANCE_PERCENT", "0.0005"))
    poll_interval = int(os.getenv("PRICE_POLL_INTERVAL_SECONDS", "2"))
    mexc_base = os.getenv("MEXC_API_BASE", "https://api.mexc.com/api/v3").rstrip("/")
    alerts_path = Path(os.getenv("ALERTS_FILE", "data/alerts.json"))

    # Ensure parent dir exists
    alerts_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_bot_token=token,
        timezone=tz,
        alert_tolerance_percent=tolerance,
        price_poll_interval_seconds=poll_interval,
        mexc_api_base=mexc_base,
        alerts_file=alerts_path,
    )

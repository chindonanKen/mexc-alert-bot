"""Configuration and settings for the MEXC Alert Bot."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    telegram_bot_token: str
    timezone: str
    alert_tolerance_percent: float
    price_poll_interval_seconds: int
    mexc_api_base: str
    alerts_file: Path

    # V3 feature flags (default OFF — production V1 path unchanged until explicitly enabled)
    feature_futures_alerts: bool
    feature_mover_scanner: bool

    # Futures price API (public contract market data)
    mexc_futures_api_base: str

    # Downside mover scanner defaults (per-user settings can override threshold/lookback)
    mover_lookback_seconds: int
    mover_threshold_percent: float
    mover_poll_seconds: int
    mover_cooldown_seconds: int
    mover_markets: str  # "futures" | "spot" | "both"

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

    futures_base = os.getenv(
        "MEXC_FUTURES_API_BASE", "https://contract.mexc.com/api/v1"
    ).rstrip("/")

    # Default "both" so mixed spot+futures watchlists work out of the box.
    # Scanner still only *fetches* markets that appear on enabled users' lists.
    mover_markets = os.getenv("MOVER_MARKETS", "both").strip().lower()
    if mover_markets not in ("futures", "spot", "both"):
        mover_markets = "both"

    # Ensure parent dir exists
    alerts_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_bot_token=token,
        timezone=tz,
        alert_tolerance_percent=tolerance,
        price_poll_interval_seconds=poll_interval,
        mexc_api_base=mexc_base,
        alerts_file=alerts_path,
        feature_futures_alerts=_env_bool("FEATURE_FUTURES_ALERTS", False),
        feature_mover_scanner=_env_bool("FEATURE_MOVER_SCANNER", False),
        mexc_futures_api_base=futures_base,
        mover_lookback_seconds=int(os.getenv("MOVER_LOOKBACK_SECONDS", "900")),
        mover_threshold_percent=float(os.getenv("MOVER_THRESHOLD_PERCENT", "5")),
        # Default 5s: evaluate rolling high→now every few seconds (floor 2s in scanner).
        # Old default 15s left more "gap" after a dump wick.
        mover_poll_seconds=int(os.getenv("MOVER_POLL_SECONDS", "5")),
        mover_cooldown_seconds=int(os.getenv("MOVER_COOLDOWN_SECONDS", "1800")),
        mover_markets=mover_markets,
    )

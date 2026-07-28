"""Configuration and settings for the MEXC Alert Bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_optional_float(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_int_tuple(name: str, default: str) -> tuple:
    raw = os.getenv(name, default)
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(out) if out else tuple(int(x) for x in default.split(",") if x.strip())


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
    # Min seconds between fires for same user+market+symbol (anti-spam only).
    # Cascade re-arm uses last fire price; this is NOT a long mute window.
    mover_cooldown_seconds: int
    # Bounce above last-fire anchor by this % clears cascade state → peak mode again
    mover_recovery_percent: float
    mover_markets: str  # "futures" | "spot" | "both"

    # Mover enrichments (scanner-owned; never touch target alerts)
    mover_enrich_velocity: bool
    mover_enrich_volume: bool
    mover_enrich_klines: bool
    mover_velocity_panic: float
    mover_velocity_fast: float
    mover_heat_auto: bool
    mover_heat_on_mw: bool
    mover_heat_breadth_min: int
    mover_heat_breadth_pct: Optional[float]  # None → 0.6 × user threshold
    mover_heat_top_n: int
    mover_heat_min_gap_seconds: float
    mover_heat_refresh_seconds: float

    # V4 learning / coach (default OFF — additive memory; never touches alerts delete path)
    feature_learning: bool
    learning_outcome_horizons_seconds: tuple  # e.g. (900, 3600, 14400)
    learning_outcome_poll_seconds: float
    # Placeholders for later phases (wired false until implemented)
    feature_news_monitor: bool
    feature_voice: bool

    @property
    def alerts_file_path(self) -> Path:
        return self.alerts_file


def load_settings() -> Settings:
    """Load settings from environment. Raises if required values are missing.

    Load order: process env wins (python-dotenv default). Then optional
    MEXC_BOT_ENV_FILE, then .env. Staging scripts export vars first and set
    ALERTS_FILE to data-staging so prod ./data is never opened.
    """
    # Optional explicit file (e.g. MEXC_BOT_ENV_FILE=.env.staging) for clarity.
    explicit = os.getenv("MEXC_BOT_ENV_FILE")
    if explicit:
        load_dotenv(explicit, override=False)
    load_dotenv(override=False)

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
        mover_poll_seconds=int(os.getenv("MOVER_POLL_SECONDS", "5")),
        # Default 45s min-gap (was 1800s mute — that blocked cascade dumps).
        mover_cooldown_seconds=int(os.getenv("MOVER_COOLDOWN_SECONDS", "45")),
        mover_recovery_percent=float(os.getenv("MOVER_RECOVERY_PERCENT", "3")),
        mover_markets=mover_markets,
        # Enrichments: velocity/volume/auto-heat ON by default when movers are on;
        # klines OFF until staging validates (API + rate limits).
        mover_enrich_velocity=_env_bool("MOVER_ENRICH_VELOCITY", True),
        mover_enrich_volume=_env_bool("MOVER_ENRICH_VOLUME", True),
        mover_enrich_klines=_env_bool("MOVER_ENRICH_KLINES", False),
        mover_velocity_panic=float(os.getenv("MOVER_VELOCITY_PANIC", "2.0")),
        mover_velocity_fast=float(os.getenv("MOVER_VELOCITY_FAST", "0.8")),
        mover_heat_auto=_env_bool("MOVER_HEAT_AUTO", True),
        mover_heat_on_mw=_env_bool("MOVER_HEAT_ON_MW", True),
        mover_heat_breadth_min=int(os.getenv("MOVER_HEAT_BREADTH_MIN", "3")),
        mover_heat_breadth_pct=_env_optional_float("MOVER_HEAT_BREADTH_PCT"),
        mover_heat_top_n=int(os.getenv("MOVER_HEAT_TOP_N", "5")),
        mover_heat_min_gap_seconds=float(os.getenv("MOVER_HEAT_MIN_GAP_SECONDS", "45")),
        mover_heat_refresh_seconds=float(os.getenv("MOVER_HEAT_REFRESH_SECONDS", "90")),
        feature_learning=_env_bool("FEATURE_LEARNING", False),
        learning_outcome_horizons_seconds=_env_int_tuple(
            "LEARNING_OUTCOME_HORIZONS_SECONDS", "900,3600,14400"
        ),
        learning_outcome_poll_seconds=float(
            os.getenv("LEARNING_OUTCOME_POLL_SECONDS", "60")
        ),
        feature_news_monitor=_env_bool("FEATURE_NEWS_MONITOR", False),
        feature_voice=_env_bool("FEATURE_VOICE", False),
    )

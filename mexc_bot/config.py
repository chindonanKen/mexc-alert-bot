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
    # Same-price / micro-move suppress (fraction, e.g. 0.002 = 0.2%)
    mover_dedupe_price_eps: float
    # Within this window after a fire, suppress if price still within eps of last fire
    mover_dedupe_window_seconds: float
    mover_markets: str  # "futures" | "spot" | "both"

    # Mover enrichments (scanner-owned; never touch target alerts)
    mover_enrich_velocity: bool
    mover_enrich_volume: bool
    mover_enrich_klines: bool
    # Core fire path: 1m high → later low (wicks), not last-price only
    mover_wick_fire: bool
    mover_velocity_panic: float
    mover_velocity_fast: float
    mover_heat_auto: bool
    mover_heat_on_mw: bool
    mover_heat_breadth_min: int
    mover_heat_breadth_pct: Optional[float]  # None → 0.85 × user threshold
    mover_heat_top_n: int
    mover_heat_min_gap_seconds: float
    mover_heat_refresh_seconds: float

    # V4 learning / coach (default OFF — additive memory; never touches alerts delete path)
    feature_learning: bool
    learning_outcome_horizons_seconds: tuple  # e.g. (900, 3600, 14400)
    learning_outcome_poll_seconds: float
    learning_auto_from_positions: bool
    learning_grace_seconds: float
    learning_max_pending_questions: int
    learning_engagement_poll_seconds: float
    # Only teach trades on/after this date (YYYY-MM-DD). Desk-era history.
    learning_teach_since: Optional[str]
    feature_news_monitor: bool
    news_poll_seconds: float
    news_push_unconfirmed: bool
    feature_voice: bool
    voice_stt_api_key: Optional[str]
    voice_stt_api_base: Optional[str]
    feature_mexc_private_read: bool
    mexc_api_key: Optional[str]
    mexc_api_secret: Optional[str]
    mexc_private_telegram_user_id: Optional[int]
    mexc_fill_sync_poll_seconds: float
    mexc_fill_notify: bool

    # Isolated-dump specialist (async — never blocks mover fires)
    feature_isolated_dump_agent: bool
    isolated_min_drop_pct: float
    isolated_threshold_multiplier: float
    isolated_max_heat_breadth: int
    isolated_require_fast_or_panic: bool
    isolated_cooldown_seconds: float
    isolated_notify_none: bool
    delist_radar_poll_seconds: float
    # Cause→effect scoring horizon for isolated agent (default 4h)
    isolated_outcome_horizon_seconds: int

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

    tz = os.getenv("TIMEZONE", "Asia/Manila")
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
        mover_dedupe_price_eps=float(os.getenv("MOVER_DEDUPE_PRICE_EPS", "0.002")),
        mover_dedupe_window_seconds=float(
            os.getenv("MOVER_DEDUPE_WINDOW_SECONDS", "120")
        ),
        mover_markets=mover_markets,
        # Enrichments: velocity/volume/auto-heat ON by default when movers are on;
        # klines OFF until staging validates (API + rate limits).
        mover_enrich_velocity=_env_bool("MOVER_ENRICH_VELOCITY", True),
        mover_enrich_volume=_env_bool("MOVER_ENRICH_VOLUME", True),
        mover_enrich_klines=_env_bool("MOVER_ENRICH_KLINES", False),
        mover_wick_fire=_env_bool("MOVER_WICK_FIRE", False),
        mover_velocity_panic=float(os.getenv("MOVER_VELOCITY_PANIC", "2.0")),
        mover_velocity_fast=float(os.getenv("MOVER_VELOCITY_FAST", "0.8")),
        mover_heat_auto=_env_bool("MOVER_HEAT_AUTO", True),
        mover_heat_on_mw=_env_bool("MOVER_HEAT_ON_MW", True),
        # Calmer defaults: board when market dumps near your mover %, not mild grinds
        mover_heat_breadth_min=int(os.getenv("MOVER_HEAT_BREADTH_MIN", "5")),
        mover_heat_breadth_pct=_env_optional_float("MOVER_HEAT_BREADTH_PCT"),
        mover_heat_top_n=int(os.getenv("MOVER_HEAT_TOP_N", "5")),
        mover_heat_min_gap_seconds=float(os.getenv("MOVER_HEAT_MIN_GAP_SECONDS", "120")),
        mover_heat_refresh_seconds=float(os.getenv("MOVER_HEAT_REFRESH_SECONDS", "300")),
        feature_learning=_env_bool("FEATURE_LEARNING", False),
        learning_outcome_horizons_seconds=_env_int_tuple(
            "LEARNING_OUTCOME_HORIZONS_SECONDS", "900,3600,14400"
        ),
        learning_outcome_poll_seconds=float(
            os.getenv("LEARNING_OUTCOME_POLL_SECONDS", "60")
        ),
        learning_auto_from_positions=_env_bool(
            "LEARNING_AUTO_FROM_POSITIONS", True
        ),
        learning_grace_seconds=float(
            os.getenv("LEARNING_GRACE_SECONDS", "3600")
        ),
        learning_max_pending_questions=int(
            os.getenv("LEARNING_MAX_PENDING_QUESTIONS", "2")
        ),
        learning_engagement_poll_seconds=float(
            os.getenv("LEARNING_ENGAGEMENT_POLL_SECONDS", "60")
        ),
        learning_teach_since=(
            (os.getenv("LEARNING_TEACH_SINCE") or "2026-07-01").strip() or None
        ),
        feature_news_monitor=_env_bool("FEATURE_NEWS_MONITOR", False),
        news_poll_seconds=float(os.getenv("NEWS_POLL_SECONDS", "90")),
        news_push_unconfirmed=_env_bool("NEWS_PUSH_UNCONFIRMED", False),
        feature_voice=_env_bool("FEATURE_VOICE", False),
        voice_stt_api_key=os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY"),
        voice_stt_api_base=os.getenv("VOICE_STT_API_BASE"),
        feature_mexc_private_read=_env_bool("FEATURE_MEXC_PRIVATE_READ", False),
        mexc_api_key=os.getenv("MEXC_API_KEY"),
        mexc_api_secret=os.getenv("MEXC_API_SECRET"),
        mexc_private_telegram_user_id=(
            int(os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID"))
            if (os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID") or "").strip().isdigit()
            else None
        ),
        mexc_fill_sync_poll_seconds=float(
            os.getenv("MEXC_FILL_SYNC_POLL_SECONDS", "120")
        ),
        mexc_fill_notify=_env_bool("MEXC_FILL_NOTIFY", False),
        feature_isolated_dump_agent=_env_bool("FEATURE_ISOLATED_DUMP_AGENT", False),
        isolated_min_drop_pct=float(os.getenv("ISOLATED_MIN_DROP_PCT", "8")),
        isolated_threshold_multiplier=float(
            os.getenv("ISOLATED_THRESHOLD_MULTIPLIER", "1.6")
        ),
        isolated_max_heat_breadth=int(os.getenv("ISOLATED_MAX_HEAT_BREADTH", "2")),
        isolated_require_fast_or_panic=_env_bool(
            "ISOLATED_REQUIRE_FAST_OR_PANIC", True
        ),
        isolated_cooldown_seconds=float(os.getenv("ISOLATED_COOLDOWN_SECONDS", "900")),
        isolated_notify_none=_env_bool("ISOLATED_NOTIFY_NONE", True),
        delist_radar_poll_seconds=float(os.getenv("DELIST_RADAR_POLL_SECONDS", "180")),
        isolated_outcome_horizon_seconds=int(
            os.getenv("ISOLATED_OUTCOME_HORIZON_SECONDS", "14400")
        ),
    )

"""Daily 6 AM target report: overnight hits + within-5% near misses.

Hits: ``target_fire_log`` + ``learning_events`` source=target (alerts delete on fire).
Near-miss: open rows in ``alerts`` vs MEXC klines over the report window.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from mexc_bot.movers.klines import KlineClient
from mexc_bot.reports.fire_log import TargetFireLog

logger = logging.getLogger(__name__)


@dataclass
class TargetHit:
    symbol: str
    market: str
    target_price: float
    fire_price: float
    ts: float
    reason: str = ""
    source: str = "target_fire_log"


@dataclass
class NearMiss:
    symbol: str
    market: str
    target_price: float
    closest_price: float
    closest_ts: float
    distance_pct: float  # abs(price-target)/target * 100
    alert_id: Optional[int] = None


@dataclass
class DailyTargetReport:
    user_id: int
    window_start: float
    window_end: float
    timezone: str
    near_pct: float
    hits: List[TargetHit] = field(default_factory=list)
    near_misses: List[NearMiss] = field(default_factory=list)
    open_targets: int = 0
    generated_at: float = field(default_factory=time.time)
    notes: List[str] = field(default_factory=list)

    def to_text(self) -> str:
        tz = ZoneInfo(self.timezone)

        def fmt_ts(ts: float) -> str:
            return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d %H:%M:%S %Z")

        lines = [
            "=" * 56,
            "AD DESK — DAILY TARGET REPORT",
            f"Generated: {fmt_ts(self.generated_at)}",
            f"Window:    {fmt_ts(self.window_start)} → {fmt_ts(self.window_end)}",
            f"User:      {self.user_id}",
            f"Open targets now: {self.open_targets}",
            f"Near-miss band:   {self.near_pct:g}% of target price",
            "=" * 56,
            "",
            f"1) TARGETS HIT (fired overnight) — {len(self.hits)}",
            "-" * 40,
        ]
        if not self.hits:
            lines.append("  (none)")
        else:
            for h in self.hits:
                lines.append(
                    f"  • {h.symbol} [{h.market}]  "
                    f"target={_px(h.target_price)}  fired@{_px(h.fire_price)}  "
                    f"{fmt_ts(h.ts)}"
                    + (f"  ({h.reason})" if h.reason else "")
                )
                lines.append(f"    source={h.source}")

        lines.extend(
            [
                "",
                f"2) NEAR MISSES (within {self.near_pct:g}%) — {len(self.near_misses)}",
                "-" * 40,
            ]
        )
        if not self.near_misses:
            lines.append("  (none)")
        else:
            for n in sorted(self.near_misses, key=lambda x: x.distance_pct):
                lines.append(
                    f"  • {n.symbol} [{n.market}]  target={_px(n.target_price)}"
                )
                lines.append(
                    f"    closest={_px(n.closest_price)}  "
                    f"({n.distance_pct:.2f}% away)  at {fmt_ts(n.closest_ts)}"
                )
                if n.alert_id is not None:
                    lines.append(f"    alert_id={n.alert_id}")

        if self.notes:
            lines.extend(["", "Notes:", *[f"  - {n}" for n in self.notes]])

        lines.extend(["", "=" * 56, ""])
        return "\n".join(lines)


def _px(p: float) -> str:
    ap = abs(float(p))
    if ap >= 1000:
        return f"{p:,.2f}"
    if ap >= 1:
        return f"{p:.4f}"
    if ap >= 0.01:
        return f"{p:.6f}"
    return f"{p:.8g}"


def report_window(
    *,
    now: Optional[float] = None,
    tz_name: str = "Asia/Manila",
    hour: int = 6,
) -> Tuple[float, float, str]:
    """Return (window_start, window_end, date_label) for the daily 6 AM cycle.

    Window = previous local ``hour`` → last local ``hour`` (typically 06:00→06:00
    Asia/Manila Philippine time).
    """
    tz = ZoneInfo(tz_name)
    wall = datetime.fromtimestamp(now if now is not None else time.time(), tz=tz)
    end_local = wall.replace(hour=hour, minute=0, second=0, microsecond=0)
    if wall < end_local:
        end_local = end_local - timedelta(days=1)
    start_local = end_local - timedelta(days=1)
    label = end_local.strftime("%Y-%m-%d")
    return start_local.timestamp(), end_local.timestamp(), label


def _db_path(alerts_file: Path) -> Path:
    p = Path(alerts_file)
    if str(p).endswith(".json"):
        return p.with_suffix(".db")
    return p


def load_open_alerts(db: Path, user_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, symbol, price, market, enabled FROM alerts "
            "WHERE user_id = ? AND enabled = 1 ORDER BY id ASC",
            (int(user_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_learning_target_hits(
    db: Path, user_id: int, t0: float, t1: float
) -> List[TargetHit]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    out: List[TargetHit] = []
    try:
        try:
            rows = conn.execute(
                "SELECT symbol, market, price, ref_price, ts, mode FROM learning_events "
                "WHERE user_id = ? AND source = 'target' AND ts >= ? AND ts < ? "
                "ORDER BY ts ASC",
                (int(user_id), float(t0), float(t1)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        for r in rows:
            out.append(
                TargetHit(
                    symbol=str(r["symbol"] or "").upper(),
                    market=str(r["market"] or "spot").lower(),
                    target_price=float(r["ref_price"] or 0),
                    fire_price=float(r["price"] or 0),
                    ts=float(r["ts"]),
                    reason=str(r["mode"] or ""),
                    source="learning_events",
                )
            )
    finally:
        conn.close()
    return out


def merge_hits(*groups: Sequence[TargetHit]) -> List[TargetHit]:
    """Dedupe hits by (symbol, market, minute). Prefer fire_log."""
    seen = set()
    ordered: List[TargetHit] = []
    for g in groups:
        ordered.extend(g)
    ordered.sort(key=lambda h: (0 if h.source == "target_fire_log" else 1, h.ts))
    out: List[TargetHit] = []
    for h in ordered:
        key = (h.symbol, h.market, int(h.ts // 60))
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    out.sort(key=lambda h: h.ts)
    return out


def closest_approach_in_bars(
    bars: List[dict],
    target: float,
    t0: float,
    t1: float,
) -> Optional[Tuple[float, float, float]]:
    """Return (closest_price, closest_ts, distance_pct) or None."""
    if not target or target == 0:
        return None
    best: Optional[Tuple[float, float, float]] = None  # dist, price, ts
    for b in bars:
        ts = float(b.get("ts") or 0)
        if ts < t0 or ts >= t1:
            continue
        for key in ("h", "l", "c", "o"):
            try:
                px = float(b[key])
            except (KeyError, TypeError, ValueError):
                continue
            dist = abs(px - target) / abs(target) * 100.0
            if best is None or dist < best[0]:
                best = (dist, px, ts)
    if best is None:
        return None
    return best[1], best[2], best[0]


def compute_near_misses(
    alerts: List[Dict[str, Any]],
    *,
    t0: float,
    t1: float,
    near_pct: float = 5.0,
    klines: Optional[KlineClient] = None,
    interval: str = "5m",
) -> List[NearMiss]:
    """For each open alert, find closest price in window; keep if within near_pct."""
    client = klines or KlineClient()
    own_client = klines is None
    hours = max(1.0, (t1 - t0) / 3600.0)
    limit = int(min(500, max(50, hours * 12 + 24)))  # 5m → 12/hour
    misses: List[NearMiss] = []
    try:
        for a in alerts:
            symbol = str(a.get("symbol") or "").upper()
            market = str(a.get("market") or "spot").lower()
            target = float(a.get("price") or 0)
            if not symbol or target <= 0:
                continue
            bars = client.get_ohlcv(market, symbol, interval, limit=limit)
            approach = closest_approach_in_bars(bars, target, t0, t1)
            if approach is None:
                continue
            closest_px, closest_ts, dist_pct = approach
            if dist_pct <= float(near_pct) + 1e-9:
                misses.append(
                    NearMiss(
                        symbol=symbol,
                        market=market,
                        target_price=target,
                        closest_price=closest_px,
                        closest_ts=closest_ts,
                        distance_pct=dist_pct,
                        alert_id=int(a["id"]) if a.get("id") is not None else None,
                    )
                )
    finally:
        if own_client:
            client.close()
    return misses


def generate_daily_target_report(
    *,
    db_path: Path,
    user_id: int,
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
    tz_name: str = "Asia/Manila",
    report_hour: int = 6,
    near_pct: float = 5.0,
    klines: Optional[KlineClient] = None,
) -> DailyTargetReport:
    db = _db_path(db_path)
    if window_start is None or window_end is None:
        t0, t1, _ = report_window(tz_name=tz_name, hour=report_hour)
    else:
        t0, t1 = float(window_start), float(window_end)

    fire_log = TargetFireLog(db)
    log_hits = [
        TargetHit(
            symbol=str(r["symbol"]).upper(),
            market=str(r.get("market") or "spot").lower(),
            target_price=float(r["target_price"]),
            fire_price=float(r["fire_price"]),
            ts=float(r["ts"]),
            reason=str(r.get("reason") or ""),
            source="target_fire_log",
        )
        for r in fire_log.hits_between(user_id, t0, t1)
    ]
    learn_hits = load_learning_target_hits(db, user_id, t0, t1)
    hits = merge_hits(log_hits, learn_hits)

    open_alerts = load_open_alerts(db, user_id)
    notes: List[str] = []
    try:
        near = compute_near_misses(
            open_alerts,
            t0=t0,
            t1=t1,
            near_pct=near_pct,
            klines=klines,
        )
    except Exception as e:
        logger.exception("near-miss computation failed: %s", e)
        near = []
        notes.append(f"Near-miss scan error: {e}")

    return DailyTargetReport(
        user_id=int(user_id),
        window_start=t0,
        window_end=t1,
        timezone=tz_name,
        near_pct=float(near_pct),
        hits=hits,
        near_misses=near,
        open_targets=len(open_alerts),
        notes=notes,
    )


def write_report_file(report: DailyTargetReport, out_dir: Path, date_label: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"daily_targets_{date_label}.txt"
    path.write_text(report.to_text(), encoding="utf-8")
    roll = out_dir / "daily_targets.log"
    with roll.open("a", encoding="utf-8") as f:
        f.write(report.to_text())
        f.write("\n")
    return path


def maybe_telegram_report(text: str, user_id: int, token: Optional[str]) -> bool:
    if not token or not user_id:
        return False
    try:
        import requests

        chunks: List[str] = []
        body = text.strip()
        while body:
            chunks.append(body[:4000])
            body = body[4000:]
        for chunk in chunks:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": int(user_id),
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
            if r.status_code != 200:
                logger.warning(
                    "telegram report send failed: %s %s",
                    r.status_code,
                    r.text[:200],
                )
                return False
        return True
    except Exception as e:
        logger.warning("telegram report failed: %s", e)
        return False


def run_daily_target_report(
    *,
    db_path: Optional[Path] = None,
    user_id: Optional[int] = None,
    tz_name: Optional[str] = None,
    report_hour: Optional[int] = None,
    near_pct: Optional[float] = None,
    out_dir: Optional[Path] = None,
    send_telegram: Optional[bool] = None,
    telegram_token: Optional[str] = None,
    now: Optional[float] = None,
) -> DailyTargetReport:
    """CLI / scheduler entry: generate, write log, optional Telegram."""
    db = Path(db_path or os.getenv("ALERTS_FILE", "data/alerts.json"))
    if str(db).endswith(".json"):
        db = db.with_suffix(".db")
    uid = user_id
    if uid is None:
        env = os.getenv("DESK_USER_ID") or os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID")
        uid = int(env) if env and str(env).strip().isdigit() else 0
    if not uid:
        raise ValueError("DESK_USER_ID required for daily target report")

    # Prefer dedicated report TZ; fall back to TIMEZONE then Manila (PHT, UTC+8)
    tz = (
        tz_name
        or os.getenv("DAILY_TARGET_REPORT_TZ")
        or os.getenv("TIMEZONE")
        or "Asia/Manila"
    )
    hour = int(
        report_hour
        if report_hour is not None
        else os.getenv("DAILY_TARGET_REPORT_HOUR", "6")
    )
    band = float(
        near_pct
        if near_pct is not None
        else os.getenv("DAILY_TARGET_NEAR_PCT", "5")
    )
    if now is not None:
        t1 = float(now)
        t0 = t1 - 86400.0
        label = datetime.fromtimestamp(t1, tz=ZoneInfo(tz)).strftime("%Y-%m-%d")
    else:
        t0, t1, label = report_window(tz_name=tz, hour=hour)

    report = generate_daily_target_report(
        db_path=db,
        user_id=int(uid),
        window_start=t0,
        window_end=t1,
        tz_name=tz,
        report_hour=hour,
        near_pct=band,
    )

    data_root = db.parent
    reports_dir = Path(
        out_dir
        or os.getenv("DAILY_TARGET_REPORT_DIR")
        or (data_root / "reports")
    )
    path = write_report_file(report, reports_dir, label)
    logger.info("Daily target report written: %s", path)

    do_tg = send_telegram
    if do_tg is None:
        do_tg = os.getenv("DAILY_TARGET_REPORT_TELEGRAM", "true").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
    if do_tg:
        maybe_telegram_report(report.to_text(), int(uid), token)

    return report


def seconds_until_next_local_hour(
    hour: int, tz_name: str, now: Optional[float] = None
) -> float:
    tz = ZoneInfo(tz_name)
    wall = datetime.fromtimestamp(now if now is not None else time.time(), tz=tz)
    nxt = wall.replace(hour=hour, minute=0, second=0, microsecond=0)
    if wall >= nxt:
        nxt = nxt + timedelta(days=1)
    return max(1.0, (nxt - wall).total_seconds())


def start_daily_report_thread(settings, stop_event) -> None:
    """Background: sleep until local report hour, run, repeat. Soft-fails."""
    import threading

    tz = (
        os.getenv("DAILY_TARGET_REPORT_TZ")
        or getattr(settings, "timezone", None)
        or os.getenv("TIMEZONE")
        or "Asia/Manila"
    )
    hour = int(os.getenv("DAILY_TARGET_REPORT_HOUR", "6"))
    uid_env = os.getenv("DESK_USER_ID") or os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID")
    if not uid_env or not str(uid_env).strip().isdigit():
        logger.warning("Daily target report thread not started: no DESK_USER_ID")
        return

    def loop() -> None:
        logger.info(
            "Daily target report scheduler started (hour=%s tz=%s)", hour, tz
        )
        while not stop_event.is_set():
            try:
                wait = seconds_until_next_local_hour(hour, tz)
                # Sleep in chunks so stop_event is responsive
                end = time.time() + wait
                while time.time() < end and not stop_event.is_set():
                    time.sleep(min(30.0, end - time.time()))
                if stop_event.is_set():
                    break
                run_daily_target_report(
                    db_path=settings.alerts_file_path,
                    user_id=int(uid_env),
                    tz_name=tz,
                    report_hour=hour,
                    telegram_token=settings.telegram_bot_token,
                )
            except Exception as e:
                logger.exception("Daily target report failed: %s", e)
                time.sleep(60)

    t = threading.Thread(target=loop, name="daily-target-report", daemon=True)
    t.start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass
    rep = run_daily_target_report()
    print(rep.to_text())

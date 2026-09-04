"""Print feed — synthetic helpers + live MEXC klines (no invented ticks)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

MEXC_API = "https://api.mexc.com"
DEFAULT_LIVE_NAMES = ("SYNUSDT", "AGIUSDT", "USUSDT")


@dataclass
class Print:
    name: str
    price: float
    volume_usd: float = 0.0
    ts: datetime | None = None
    chosen_tf_reds: int = 0
    faster_tf_reds: dict[str, int] = field(default_factory=dict)
    low: float | None = None  # candle low for met checks
    weak_bounce: bool = False  # optional override; prefer scored bounce kind when facts exist
    candles_since_ad_tag: int | None = None  # TF candles since AD tag (Reed/tape)
    source: str = "synthetic"  # synthetic | mexc
    open_time_ms: int | None = None

    def __post_init__(self) -> None:
        if self.ts is None:
            self.ts = datetime.now(timezone.utc)
        if self.low is None:
            self.low = self.price


def iter_prints(prints: list[Print]) -> Iterator[Print]:
    yield from prints


def load_print_file(path: str) -> list[Print]:
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text())
    out: list[Print] = []
    for row in data:
        out.append(
            Print(
                name=row["name"],
                price=float(row["price"]),
                volume_usd=float(row.get("volume_usd", 0)),
                chosen_tf_reds=int(row.get("chosen_tf_reds", 0)),
                faster_tf_reds=dict(row.get("faster_tf_reds") or {}),
                low=float(row["low"]) if "low" in row else None,
                weak_bounce=bool(row.get("weak_bounce", False)),
                candles_since_ad_tag=(
                    int(row["candles_since_ad_tag"])
                    if row.get("candles_since_ad_tag") is not None
                    else None
                ),
                source=str(row.get("source") or "synthetic"),
                open_time_ms=int(row["open_time_ms"]) if row.get("open_time_ms") is not None else None,
            )
        )
    return out


def descending_dump(
    name: str,
    start: float,
    end: float,
    steps: int = 10,
    *,
    volume_usd: float = 50_000,
    reds_ramp: bool = True,
    faster_tf: str | None = "5m",
) -> list[Print]:
    """Tiny synthetic dump for staff scoring."""
    if steps < 2:
        steps = 2
    prints: list[Print] = []
    for i in range(steps):
        t = i / (steps - 1)
        px = start + (end - start) * t
        reds = i + 1 if reds_ramp else 0
        faster = {faster_tf: max(1, i)} if faster_tf else {}
        prints.append(
            Print(
                name=name,
                price=px,
                volume_usd=volume_usd if i >= steps // 2 else volume_usd * 0.2,
                chosen_tf_reds=reds,
                faster_tf_reds=faster,
                low=px,
                source="synthetic",
            )
        )
    return prints


def ascending_bounce(
    name: str,
    start: float,
    end: float,
    steps: int = 12,
    *,
    volume_usd: float = 80_000,
    chosen_tf_reds: int = 0,
    faster_tf: str | None = "1h",
) -> list[Print]:
    """Synthetic bounce prints (price rises). For money-sample sells — not live ticks."""
    if steps < 2:
        steps = 2
    prints: list[Print] = []
    for i in range(steps):
        t = i / (steps - 1)
        px = start + (end - start) * t
        faster = {faster_tf: 0} if faster_tf else {}
        prints.append(
            Print(
                name=name,
                price=px,
                volume_usd=volume_usd,
                chosen_tf_reds=chosen_tf_reds,
                faster_tf_reds=faster,
                low=min(start, px),
                source="synthetic",
            )
        )
    return prints


def print_to_dict(p: Print) -> dict[str, Any]:
    return {
        "name": p.name,
        "price": p.price,
        "volume_usd": p.volume_usd,
        "ts": p.ts.isoformat() if p.ts else None,
        "chosen_tf_reds": p.chosen_tf_reds,
        "faster_tf_reds": p.faster_tf_reds,
        "low": p.low,
        "source": p.source,
        "open_time_ms": p.open_time_ms,
    }


# --- MEXC live (klines only; never invent prices) ---


def trailing_red_count(klines: list[list[Any]]) -> int:
    """Count consecutive red candles from the newest bar backward (close < open)."""
    n = 0
    for row in reversed(klines):
        o = float(row[1])
        c = float(row[4])
        if c < o:
            n += 1
        else:
            break
    return n


def fetch_mexc_klines(
    symbol: str,
    interval: str = "1m",
    limit: int = 5,
    *,
    client: httpx.Client | None = None,
    base_url: str = MEXC_API,
) -> list[list[Any]]:
    """GET /api/v3/klines. Returns [] on failure — caller must not invent ticks."""
    own = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        r = http.get(
            f"{base_url}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        return data
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    finally:
        if own:
            http.close()


def print_from_klines(
    name: str,
    price_klines: list[list[Any]],
    *,
    chosen_tf_klines: list[list[Any]] | None = None,
    faster_tf: str = "1h",
    faster_tf_klines: list[list[Any]] | None = None,
) -> Print | None:
    """
    Convert real MEXC kline rows into one engine Print.
    Uses the newest price candle only. Returns None if no usable rows (no invent).
    """
    if not price_klines:
        return None
    row = price_klines[-1]
    try:
        open_ms = int(row[0])
        price = float(row[4])  # close
        low = float(row[3])
        # Prefer chosen-TF bar quote volume for Path/Size; 1m forming bar can read $0.
        vol_row = (chosen_tf_klines[-1] if chosen_tf_klines else row)
        volume_usd = float(vol_row[7]) if len(vol_row) > 7 else float(vol_row[5]) * price
    except (IndexError, TypeError, ValueError):
        return None
    if price <= 0:
        return None
    chosen_reds = trailing_red_count(chosen_tf_klines or [])
    faster_reds = trailing_red_count(faster_tf_klines or [])
    return Print(
        name=name,
        price=price,
        volume_usd=volume_usd,
        ts=datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc),
        chosen_tf_reds=chosen_reds,
        faster_tf_reds={faster_tf: faster_reds} if faster_tf else {},
        low=low,
        source="mexc",
        open_time_ms=open_ms,
    )


@dataclass
class MexcLiveFeed:
    """Poll api.mexc.com klines for hung names. Short interval; no invented ticks."""

    names: tuple[str, ...] | list[str] = DEFAULT_LIVE_NAMES
    price_interval: str = "1m"
    chosen_tf: str = "4h"
    faster_tf: str = "1h"
    # Per-name (chosen_tf, faster_tf) from hung plays. Missing name → class defaults.
    name_tfs: dict[str, tuple[str, str]] = field(default_factory=dict)
    price_limit: int = 3
    tf_limit: int = 30
    base_url: str = MEXC_API
    client: httpx.Client | None = None
    _last_fingerprint: dict[str, tuple[int, float, float]] = field(default_factory=dict)

    def tfs_for(self, name: str) -> tuple[str, str]:
        """Resolve this name's chosen + faster intervals. Fallback 4h / 1h."""
        pair = self.name_tfs.get(name)
        if not pair:
            return self.chosen_tf, self.faster_tf
        chosen = (pair[0] or "").strip() or self.chosen_tf
        faster = (pair[1] or "").strip() or self.faster_tf
        return chosen, faster

    def poll_once(self) -> list[Print]:
        """Fetch each name once. Skip names with no API data. Dedupe identical bar fingerprint."""
        out: list[Print] = []
        http = self.client
        own = http is None
        if own:
            http = httpx.Client(timeout=15.0)
        try:
            for name in self.names:
                chosen_tf, faster_tf = self.tfs_for(name)
                px_rows = fetch_mexc_klines(
                    name,
                    self.price_interval,
                    self.price_limit,
                    client=http,
                    base_url=self.base_url,
                )
                chosen_rows = fetch_mexc_klines(
                    name,
                    chosen_tf,
                    self.tf_limit,
                    client=http,
                    base_url=self.base_url,
                )
                faster_rows = fetch_mexc_klines(
                    name,
                    faster_tf,
                    self.tf_limit,
                    client=http,
                    base_url=self.base_url,
                )
                pr = print_from_klines(
                    name,
                    px_rows,
                    chosen_tf_klines=chosen_rows,
                    faster_tf=faster_tf,
                    faster_tf_klines=faster_rows,
                )
                if pr is None or pr.open_time_ms is None:
                    continue
                fp = (pr.open_time_ms, pr.price, pr.volume_usd)
                if self._last_fingerprint.get(name) == fp:
                    continue
                self._last_fingerprint[name] = fp
                out.append(pr)
        finally:
            if own and http is not None:
                http.close()
        return out

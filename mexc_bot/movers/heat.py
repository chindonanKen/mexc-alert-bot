"""Watchlist heat ranking + widespread-panic board helpers.

Scanner-owned: auto push uses this; /mw can render the same snapshot.
Never touches AlertStore / target alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .history import PriceHistory
from .velocity import score_dump


@dataclass
class HeatRow:
    market: str
    symbol: str
    dd_pct: float  # negative when dumping, e.g. -7.2
    peak: float
    price_now: float
    peak_ts: float
    velocity: float  # % per min
    band: str
    ready: bool = True


@dataclass
class HeatBoard:
    lookback_seconds: float
    ranked: List[HeatRow] = field(default_factory=list)
    warming: List[Tuple[str, str]] = field(default_factory=list)  # (market, symbol)
    dumping_count: int = 0
    watchlist_count: int = 0

    @property
    def breadth_frac(self) -> str:
        return f"{self.dumping_count}/{self.watchlist_count}"


def heat_snapshot(
    history: PriceHistory,
    watchlist: Sequence[Dict[str, Any]],
    lookback_seconds: float,
    now: Optional[float] = None,
    *,
    panic_per_min: float = 2.0,
    fast_per_min: float = 0.8,
    breadth_pct: float = 5.0,
) -> HeatBoard:
    """
    Rank watchlist by peak drawdown (worst first), then velocity.

    breadth_pct: absolute % drawdown (e.g. 3.0 means −3%) counting as "dumping"
    for panic-board breadth.
    """
    import time as _time

    now = now if now is not None else _time.time()
    board = HeatBoard(lookback_seconds=float(lookback_seconds))
    board.watchlist_count = len(watchlist)
    ready_rows: List[HeatRow] = []

    for item in watchlist:
        market = str(item.get("market") or "futures").lower()
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        dd = history.peak_drawdown(market, symbol, lookback_seconds, now=now)
        if dd is None:
            board.warming.append((market, symbol))
            continue
        change, peak, price_now, peak_ts = dd
        vel, _mins, band = score_dump(
            peak_ts,
            peak,
            now,
            price_now,
            panic_per_min=panic_per_min,
            fast_per_min=fast_per_min,
        )
        row = HeatRow(
            market=market,
            symbol=symbol,
            dd_pct=change * 100.0,
            peak=peak,
            price_now=price_now,
            peak_ts=peak_ts,
            velocity=vel,
            band=band,
            ready=True,
        )
        ready_rows.append(row)
        if change * 100.0 <= -abs(breadth_pct):
            board.dumping_count += 1

    # Worst drawdown first; on tie, higher velocity first
    ready_rows.sort(key=lambda r: (r.dd_pct, -r.velocity))
    board.ranked = ready_rows
    return board


def is_widespread_panic(board: HeatBoard, breadth_min: int) -> bool:
    """True when enough watchlist symbols are dumping at breadth threshold."""
    if breadth_min <= 0:
        return False
    return board.dumping_count >= breadth_min


def board_fingerprint(rows: Sequence[HeatRow], top_n: int) -> Tuple:
    """Stable tuple for anti-spam refresh detection."""
    out = []
    for r in list(rows)[: max(0, top_n)]:
        out.append((r.market, r.symbol, round(r.dd_pct, 1), r.band))
    return tuple(out)


def format_heat_board_html(
    board: HeatBoard,
    *,
    top_n: int = 5,
    title: str = "PANIC BOARD",
) -> str:
    import html as _html

    window = _format_lookback(board.lookback_seconds)
    lines = [
        f"🔥 <b>{_html.escape(title)}</b> ({window}) · {board.breadth_frac} dumping",
    ]
    for i, r in enumerate(board.ranked[:top_n], start=1):
        tag = "F" if r.market == "futures" else "S"
        sym = _html.escape(r.symbol)
        lines.append(
            f"{i}. [{tag}] <b>{sym}</b>  {r.dd_pct:.1f}%  · {r.band}"
        )
    if not board.ranked[:top_n]:
        lines.append("(no ranked symbols yet)")
    if board.warming:
        lines.append(f"warming up: {len(board.warming)} symbol(s)")
    lines.append("Open the top chart first. /mw for full list.")
    return "\n".join(lines)


def format_heat_plain(board: HeatBoard, *, top_n: int = 20) -> List[str]:
    """Plain-text lines for /mw command replies."""
    window = _format_lookback(board.lookback_seconds)
    lines = [f"HEAT ({window} peak→now) · {board.breadth_frac} dumping"]
    if not board.ranked:
        lines.append("  (no ranked symbols — still warming up)")
    for i, r in enumerate(board.ranked[:top_n], start=1):
        tag = "F" if r.market == "futures" else "S"
        lines.append(f"  {i}. [{tag}] {r.symbol}  {r.dd_pct:.1f}%  · {r.band}")
    if board.warming:
        names = ", ".join(f"{m}:{s}" for m, s in board.warming[:12])
        more = f" +{len(board.warming) - 12}" if len(board.warming) > 12 else ""
        lines.append(f"  warming up: {names}{more}")
    return lines


def _format_lookback(lookback: float) -> str:
    if lookback < 60:
        return f"{int(lookback)}s"
    minutes = lookback / 60.0
    if abs(minutes - round(minutes)) < 1e-6:
        return f"{int(round(minutes))}m"
    return f"{minutes:.1f}m"

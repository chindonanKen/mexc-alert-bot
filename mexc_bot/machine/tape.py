"""Official MEXC tape for the Machine book. Public REST only. Never invent ticks."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .hang import official_volume_n, volume_label

logger = logging.getLogger(__name__)


def _as_price(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        px = float(raw)
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    return px


def last_bar_close(bars: Optional[Sequence[Dict[str, Any]]]) -> Optional[float]:
    if not bars:
        return None
    last = bars[-1] if isinstance(bars[-1], dict) else None
    if not last:
        return None
    return _as_price(last.get("c") if last.get("c") is not None else last.get("close"))


def official_last_price(
    *,
    ticker: Any = None,
    bars: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[float]:
    """Ticker first, else last official kline close. Never guess / never use AD."""
    px = _as_price(ticker)
    if px is not None:
        return px
    return last_bar_close(bars)


def official_reds(bars: Optional[Iterable[Dict[str, Any]]]) -> Optional[int]:
    """Closed red streak; forming adds one only if it is already red.

    No bars → None (unknown). Do not invent a streak.
    """
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if not seq:
        return None

    def _red(bar: Dict[str, Any]) -> bool:
        try:
            o = float(bar.get("o") if bar.get("o") is not None else bar.get("open"))
            c = float(bar.get("c") if bar.get("c") is not None else bar.get("close"))
        except (TypeError, ValueError):
            return False
        return c < o

    forming = seq[-1]
    n = 0
    for bar in reversed(seq[:-1]):
        if _red(bar):
            n += 1
        else:
            break
    if _red(forming):
        n += 1
    return n


def fetch_official_ticker(
    market: str,
    symbol: str,
    *,
    spot=None,
    futures=None,
) -> Optional[float]:
    """One official MEXC last. Soft-fail → None. No Binance fallback."""
    try:
        if str(market).lower() == "futures":
            if futures is None:
                from ..exchange import MexcFuturesClient

                futures = MexcFuturesClient()
            return _as_price(futures.get_price(symbol))
        if spot is None:
            from ..exchange import MexcClient

            spot = MexcClient()
        return _as_price(spot.get_price(symbol))
    except Exception as e:
        logger.debug("machine ticker %s %s: %s", market, symbol, e)
        return None


def fetch_official_klines(
    market: str,
    symbol: str,
    tf: str,
    *,
    client=None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Plan-TF bars including the forming candle. Soft-fail → []."""
    try:
        if client is None:
            from ..movers.klines import KlineClient

            client = KlineClient(cache_ttl=8.0)
        bars = client.get_ohlcv(
            market, symbol, tf or "15m", limit=limit, include_forming=True
        )
        return [b for b in (bars or []) if isinstance(b, dict)]
    except Exception as e:
        logger.debug("machine klines %s %s %s: %s", market, symbol, tf, e)
        return []


def fatal_news_hits(db_path, symbol: str) -> List[Dict[str, Any]]:
    """Delist/scam/hack/closure only. Rumors stay out of news_kill."""
    try:
        from ..learning.fatal_news import lookup_fatal_for_ticker

        pack = lookup_fatal_for_ticker(db_path, symbol, include_unconfirmed=False)
        return list(pack.get("hits") or [])
    except Exception as e:
        logger.debug("machine news %s: %s", symbol, e)
        return []


def _bar_green(bar: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(bar, dict):
        return False
    try:
        o = float(bar.get("o") if bar.get("o") is not None else bar.get("open"))
        c = float(bar.get("c") if bar.get("c") is not None else bar.get("close"))
    except (TypeError, ValueError):
        return False
    return c > o


def panic_up_from_tape(
    plan: Dict[str, Any],
    *,
    last: Optional[float],
    bars_1m: Optional[Sequence[Dict[str, Any]]] = None,
    faster_bars: Optional[Sequence[Dict[str, Any]]] = None,
    vol_usd_fast: Optional[float] = None,
) -> bool:
    """Live, last above the fill/AD, green fast tape, dollar spike. Not a guessed target."""
    from .facts import typical_dollar_volume, VOL_SPIKE

    if not plan.get("live"):
        return False
    floor = None
    for raw in (plan.get("leftover_avg"), plan.get("ad_bottom")):
        try:
            if raw is not None and float(raw) > 0:
                floor = float(raw)
                break
        except (TypeError, ValueError):
            continue
    if last is None or floor is None or last <= floor:
        return False
    green = False
    if bars_1m:
        green = _bar_green(bars_1m[-1] if bars_1m else None)
    if not green and faster_bars:
        green = _bar_green(faster_bars[-1])
    if not green:
        return False
    habit = typical_dollar_volume(bars_1m)
    spike = False
    if vol_usd_fast is not None and habit and habit > 0:
        spike = float(vol_usd_fast) >= VOL_SPIKE * habit
    if not spike:
        lab = volume_label(bars_1m)
        spike = lab in {"elevated", "climax"}
    return spike


def snapshot_for_plan(
    plan: Dict[str, Any],
    *,
    ticker: Any = None,
    bars: Optional[Sequence[Dict[str, Any]]] = None,
    bars_1m: Optional[Sequence[Dict[str, Any]]] = None,
    faster_bars: Optional[Sequence[Dict[str, Any]]] = None,
    faster_tf: Optional[str] = None,
    news: Optional[List[Dict[str, Any]]] = None,
    heat_breadth: Optional[int] = None,
    panic_board: bool = False,
    board: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one evaluate snapshot from official tape pieces only."""
    from .facts import dollar_volume, faster_tf_for

    tf = plan.get("tf") or "15m"
    last = official_last_price(ticker=ticker, bars=bars_1m or bars)
    reds = official_reds(bars)
    snap: Dict[str, Any] = {}
    if last is not None:
        snap["last_price"] = last
    reds_map: Dict[str, Any] = {}
    if bars:
        snap["bars"] = list(bars)
        snap["volume"] = volume_label(bars)
        vn = official_volume_n(bars)
        if vn is not None:
            snap["volume_n"] = vn
        vol_play = dollar_volume(bars)
        if vol_play is not None:
            snap["vol_usd_play"] = vol_play
    if reds is not None:
        reds_map[str(tf)] = reds
    if bars_1m:
        snap["bars_1m"] = list(bars_1m)
        vol_fast = dollar_volume(bars_1m)
        if vol_fast is not None:
            snap["vol_usd_fast"] = vol_fast
        r1 = official_reds(bars_1m)
        if r1 is not None:
            reds_map["1m"] = r1
    if faster_bars:
        ftf = str(faster_tf or faster_tf_for(tf))
        snap["faster_bars"] = list(faster_bars)
        fr = official_reds(faster_bars)
        if fr is not None:
            reds_map[ftf] = fr
    if reds_map:
        snap["reds"] = reds_map
    if news:
        snap["news"] = list(news)
    if heat_breadth is not None:
        snap["heat_breadth"] = heat_breadth
    if panic_board:
        snap["panic_board"] = True
    if board:
        snap["board"] = dict(board)
    from .facts import play_from_row

    play = play_from_row(plan)
    if last is not None:
        for raw in play.get("unmet_bases") or []:
            try:
                if last >= float(raw):
                    snap["into_base"] = True
                    break
            except (TypeError, ValueError):
                continue
        typical = play.get("typical_bounce") or play.get("bounce_run")
        fill = plan.get("leftover_avg")
        try:
            if typical is not None and fill is not None and last >= float(fill) + float(typical):
                snap["bounce_strong"] = True
        except (TypeError, ValueError):
            pass
    if panic_up_from_tape(
        plan,
        last=last,
        bars_1m=bars_1m,
        faster_bars=faster_bars,
        vol_usd_fast=snap.get("vol_usd_fast"),
    ):
        snap["panic_up_volume"] = True
    return snap

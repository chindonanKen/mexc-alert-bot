"""Official MEXC tape for the Machine book. Public REST only. Never invent ticks."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .hang import official_volume_n, volume_label

logger = logging.getLogger(__name__)

_kline_client = None
_px_hist: Dict[str, List[Tuple[float, float]]] = {}
_BOARD_LOOKBACK = 900.0
_FAST_DD = 0.03


def _kline_cli():
    global _kline_client
    if _kline_client is None:
        from ..movers.klines import KlineClient

        _kline_client = KlineClient(timeout=2.5, cache_ttl=2.5)
    return _kline_client


def note_board_price(key: str, px: Optional[float], now: Optional[float] = None) -> None:
    if px is None or px <= 0:
        return
    ts = float(now if now is not None else time.time())
    buf = _px_hist.setdefault(str(key), [])
    buf.append((ts, float(px)))
    cut = ts - _BOARD_LOOKBACK
    _px_hist[str(key)] = [(t, p) for t, p in buf if t >= cut]


def board_name_is_fast(key: str, px: Optional[float]) -> bool:
    if px is None or px <= 0:
        return False
    buf = _px_hist.get(str(key)) or []
    if not buf:
        return False
    hi = max(p for _, p in buf)
    if hi <= 0:
        return False
    return (hi - float(px)) / hi >= _FAST_DD


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


_hung_last: Dict[str, float] = {}
_trade_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def fetch_recent_trades(
    market: str,
    symbol: str,
    *,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    """Public last trades. Soft-fail → []. Times in seconds."""
    key = f"{market}|{symbol}"
    now = time.time()
    hit = _trade_cache.get(key)
    if hit and now - hit[0] < 1.2:
        return hit[1]
    out: List[Dict[str, Any]] = []
    try:
        cli = _kline_cli()
        if str(market).lower() == "futures":
            url = f"{cli.futures_base}/contract/deals/{symbol}"
            resp = cli.session.get(url, params={"limit": int(limit)}, timeout=2.0)
            data = resp.json() if resp.status_code == 200 else {}
            rows = data.get("data") if isinstance(data, dict) else data
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                px = _as_price(row.get("p") if row.get("p") is not None else row.get("price"))
                ts = row.get("T") if row.get("T") is not None else row.get("t")
                qty = row.get("v") if row.get("v") is not None else row.get("volume")
                if px is None:
                    continue
                try:
                    tsec = float(ts) / (1000.0 if float(ts) > 1e12 else 1.0)
                    q = float(qty or 0) * px
                except (TypeError, ValueError):
                    continue
                out.append({"ts": tsec, "price": px, "quote": q})
        else:
            url = f"{cli.spot_base}/trades"
            resp = cli.session.get(
                url,
                params={"symbol": str(symbol).upper(), "limit": int(limit)},
                timeout=2.0,
            )
            rows = resp.json() if resp.status_code == 200 else []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                px = _as_price(row.get("price"))
                if px is None:
                    continue
                try:
                    tsec = float(row.get("time") or 0) / 1000.0
                    q = float(row.get("quoteQty") or 0)
                    if q <= 0:
                        q = float(row.get("qty") or 0) * px
                except (TypeError, ValueError):
                    continue
                out.append({"ts": tsec, "price": px, "quote": q})
    except Exception as e:
        logger.debug("machine trades %s %s: %s", market, symbol, e)
        out = []
    _trade_cache[key] = (now, out)
    return out


def hung_seconds_dump(
    symbol: str,
    last: Optional[float],
    layers: Sequence[Dict[str, Any]],
    trades: Sequence[Dict[str, Any]],
    *,
    now: Optional[float] = None,
    window: float = 3.0,
) -> Dict[str, Any]:
    """Dump through a hung layer with $ volume in seconds. Not a 1m close."""
    ts = float(now if now is not None else time.time())
    key = str(symbol or "")
    prev = _hung_last.get(key)
    if last is not None:
        _hung_last[key] = float(last)
    vol = 0.0
    older = 0.0
    recent_px: List[float] = []
    for t in trades or []:
        age = ts - float(t.get("ts") or 0)
        q = float(t.get("quote") or 0)
        px = t.get("price")
        if 0 <= age <= window:
            vol += q
            try:
                if px is not None:
                    recent_px.append(float(px))
            except (TypeError, ValueError):
                pass
        elif window < age <= 60:
            older += q
    spike = False
    if older > 0:
        spike = vol >= 1.2 * (older / 52.0) * window
    elif vol > 0 and last is not None and prev is not None and last < prev:
        spike = True
    elif vol > 0 and recent_px and max(recent_px) > min(recent_px):
        spike = True
    through = False
    hi = max(recent_px) if recent_px else None
    lo = min(recent_px) if recent_px else None
    if last is not None:
        lo = float(last) if lo is None else min(float(lo), float(last))
        hi = float(last) if hi is None else max(float(hi), float(last))
    if prev is not None:
        hi = float(prev) if hi is None else max(float(hi), float(prev))
    if hi is not None and lo is not None:
        for layer in layers or []:
            if str(layer.get("band") or "ad") != "ad":
                continue
            try:
                lp = float(layer["price"])
            except (TypeError, ValueError, KeyError):
                continue
            if float(hi) > lp >= float(lo):
                through = True
                break
    return {
        "vol_usd": vol,
        "spike": spike,
        "through_layer": through,
        "fast_dump": bool(spike and through),
        "last": last,
        "prev": prev,
    }


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
            client = _kline_cli()
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
    trades: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build one evaluate snapshot from official tape pieces only."""
    from .facts import dollar_volume, faster_tf_for

    tf = plan.get("tf") or "15m"
    last = official_last_price(ticker=ticker, bars=bars_1m or bars)
    if trades:
        newest = max(
            (t for t in trades if isinstance(t, dict) and t.get("price") is not None),
            key=lambda t: float(t.get("ts") or 0),
            default=None,
        )
        if newest is not None:
            tp = _as_price(newest.get("price"))
            if tp is not None:
                last = tp
    reds = official_reds(bars)
    snap: Dict[str, Any] = {}
    if last is not None:
        snap["last_price"] = last
    if trades:
        import json as _json

        layers = plan.get("layers")
        if not layers:
            try:
                layers = _json.loads(plan.get("layers_json") or "[]")
            except (TypeError, ValueError):
                layers = []
        dump = hung_seconds_dump(
            str(plan.get("symbol") or ""), last, layers or [], trades
        )
        snap["trade_vol_usd"] = dump.get("vol_usd")
        if dump.get("vol_usd"):
            snap["vol_usd_fast"] = dump.get("vol_usd")
        if dump.get("fast_dump"):
            snap["trade_dump"] = True
            snap["fast_dump_volume"] = True
    reds_map: Dict[str, Any] = {}
    if bars:
        snap["bars"] = list(bars)
        snap["volume"] = volume_label(bars)
        vol_play = dollar_volume(bars)
        if vol_play is not None:
            snap["vol_usd_play"] = vol_play
    if reds is not None:
        reds_map[str(tf)] = reds
    if bars_1m:
        snap["bars_1m"] = list(bars_1m)
        vol_fast = dollar_volume(bars_1m)
        if vol_fast is not None and not snap.get("trade_dump"):
            snap["vol_usd_fast"] = vol_fast
        r1 = official_reds(bars_1m)
        if r1 is not None:
            reds_map["1m"] = r1
        if not snap.get("volume") or snap.get("volume") == "unknown":
            snap["volume"] = volume_label(bars_1m)
    vn = snap.get("vol_usd_fast") or snap.get("vol_usd_play")
    if vn is None:
        vn = official_volume_n(bars_1m or bars)
    if vn is not None:
        snap["volume_n"] = vn
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

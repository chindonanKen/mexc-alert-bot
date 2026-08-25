"""Isolated machine_* tables. Additive CREATE only. Never writes foreign books."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from ..db_safety import ensure_column

from .settings import FORBIDDEN_WRITE_TABLES, MACHINE_TABLES


def _write_guard_sql(sql: str) -> None:
    """Refuse writes that name a protected live table."""
    blob = " ".join(str(sql or "").lower().split())
    if not blob:
        return
    mutating = blob.startswith(
        ("insert ", "update ", "delete ", "drop ", "replace ", "alter ")
    )
    if not mutating:
        return
    for table in FORBIDDEN_WRITE_TABLES:
        # word boundary-ish: "into alerts " / "update alerts " / "from alerts "
        if (
            f" {table} " in f" {blob} "
            or f" {table}(" in f" {blob} "
            or blob.endswith(f" {table}")
        ):
            raise RuntimeError(
                f"AD Machine must not write {table} (isolated book only)"
            )


class MachineStore:
    """Own SQLite book: plans, simulated orders, closes, KB, needs-you."""

    def __init__(self, path: Path):
        self._lock = RLock()
        p = Path(path)
        if str(p).endswith(".json"):
            p = p.with_suffix(".db")
        self.db_path = p
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.Error:
            pass
        return conn

    def _exec(
        self,
        sql: str,
        params: tuple = (),
        *,
        fetch: str = "none",
    ) -> Any:
        _write_guard_sql(sql)
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(sql, params)
                if fetch == "all":
                    rows = [dict(r) for r in cur.fetchall()]
                    conn.commit()
                    return rows
                if fetch == "one":
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row) if row else None
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS machine_plans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        market TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        tf TEXT,
                        ad_top REAL,
                        ad_bottom REAL,
                        ad_status TEXT NOT NULL DEFAULT 'unknown',
                        ad_source TEXT,
                        ad_note TEXT,
                        bar_top_ts REAL,
                        bar_bottom_ts REAL,
                        bar_top_label TEXT,
                        bar_bottom_label TEXT,
                        initial_drop_top REAL,
                        initial_drop_bottom REAL,
                        zones_json TEXT,
                        layers_json TEXT,
                        remaining_layers INTEGER,
                        next_layer_usd REAL,
                        status TEXT NOT NULL DEFAULT 'watch',
                        live INTEGER NOT NULL DEFAULT 0,
                        resting INTEGER NOT NULL DEFAULT 0,
                        allocated_usd REAL NOT NULL DEFAULT 0,
                        armed_at REAL,
                        reds INTEGER,
                        volume TEXT,
                        volume_n REAL,
                        news TEXT,
                        gate_json TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(user_id, symbol, market)
                    );
                    CREATE TABLE IF NOT EXISTS machine_orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        plan_id INTEGER NOT NULL,
                        layer_idx INTEGER NOT NULL,
                        price REAL NOT NULL,
                        usd REAL NOT NULL,
                        qty REAL,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS machine_closes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        plan_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        market TEXT NOT NULL,
                        tf TEXT,
                        reason TEXT NOT NULL,
                        reds INTEGER,
                        volume TEXT,
                        bounce_or_fail TEXT,
                        process_ok INTEGER,
                        money_pnl REAL,
                        allocated_usd REAL,
                        payload_json TEXT,
                        closed_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS machine_kb (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        close_id INTEGER NOT NULL,
                        plan_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        market TEXT NOT NULL,
                        tf TEXT,
                        reds INTEGER,
                        volume TEXT,
                        bounce_or_fail TEXT,
                        process_ok INTEGER,
                        money_pnl REAL,
                        habit_reds INTEGER,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS machine_needs_you (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open',
                        symbol TEXT,
                        market TEXT,
                        payload_json TEXT,
                        created_at REAL NOT NULL,
                        resolved_at REAL
                    );
                    """
                )
                for col, decl in (
                    ("armed_at", "REAL"),
                    ("volume", "TEXT"),
                    ("volume_n", "REAL"),
                    ("news", "TEXT"),
                    ("gate_json", "TEXT"),
                ):
                    ensure_column(conn, "machine_plans", col, decl)
                conn.commit()
            finally:
                conn.close()

    # ---- plans ----

    def get_plan(self, user_id: int, plan_id: int) -> Optional[Dict[str, Any]]:
        return self._exec(
            "SELECT * FROM machine_plans WHERE user_id=? AND id=?",
            (int(user_id), int(plan_id)),
            fetch="one",
        )

    def get_plan_by_symbol(
        self, user_id: int, symbol: str, market: str
    ) -> Optional[Dict[str, Any]]:
        return self._exec(
            "SELECT * FROM machine_plans WHERE user_id=? AND symbol=? AND market=?",
            (int(user_id), str(symbol).upper(), str(market).lower()),
            fetch="one",
        )

    def list_plans(self, user_id: int) -> List[Dict[str, Any]]:
        return self._exec(
            "SELECT * FROM machine_plans WHERE user_id=? ORDER BY id ASC",
            (int(user_id),),
            fetch="all",
        )

    def upsert_plan(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        symbol = str(payload["symbol"]).upper()
        market = str(payload["market"]).lower()
        existing = self.get_plan_by_symbol(user_id, symbol, market)
        fields = {
            "display_name": payload.get("display_name") or symbol,
            "tf": payload.get("tf"),
            "ad_top": payload.get("ad_top"),
            "ad_bottom": payload.get("ad_bottom"),
            "ad_status": payload.get("ad_status") or "unknown",
            "ad_source": payload.get("ad_source"),
            "ad_note": payload.get("ad_note"),
            "bar_top_ts": payload.get("bar_top_ts"),
            "bar_bottom_ts": payload.get("bar_bottom_ts"),
            "bar_top_label": payload.get("bar_top_label"),
            "bar_bottom_label": payload.get("bar_bottom_label"),
            "initial_drop_top": payload.get("initial_drop_top"),
            "initial_drop_bottom": payload.get("initial_drop_bottom"),
            "zones_json": _json(payload.get("zones")),
            "layers_json": _json(payload.get("layers")),
            "remaining_layers": payload.get("remaining_layers"),
            "next_layer_usd": payload.get("next_layer_usd"),
            "status": payload.get("status") or "watch",
            "live": 1 if payload.get("live") else 0,
            "resting": 1 if payload.get("resting") else 0,
            "allocated_usd": float(payload.get("allocated_usd") or 0),
            "armed_at": payload.get("armed_at"),
            "reds": payload.get("reds"),
            "volume": payload.get("volume"),
            "news": payload.get("news"),
            "gate_json": _json(payload.get("gate")),
            "updated_at": now,
        }
        if "volume_n" in payload:
            fields["volume_n"] = payload.get("volume_n")
        elif existing:
            fields["volume_n"] = existing.get("volume_n")
        else:
            fields["volume_n"] = None
        if existing:
            sets = ", ".join(f"{k}=?" for k in fields)
            self._exec(
                f"UPDATE machine_plans SET {sets} WHERE id=? AND user_id=?",
                tuple(fields.values()) + (int(existing["id"]), int(user_id)),
            )
            row = self.get_plan(user_id, int(existing["id"]))
            assert row
            return row
        cols = (
            "user_id, symbol, market, created_at, "
            + ", ".join(fields.keys())
        )
        placeholders = ", ".join("?" for _ in range(4 + len(fields)))
        pid = self._exec(
            f"INSERT INTO machine_plans ({cols}) VALUES ({placeholders})",
            (int(user_id), symbol, market, now) + tuple(fields.values()),
        )
        row = self.get_plan(user_id, int(pid))
        assert row
        return row

    def patch_plan(self, user_id: int, plan_id: int, **fields: Any) -> Dict[str, Any]:
        if not fields:
            row = self.get_plan(user_id, plan_id)
            if not row:
                raise KeyError("plan not found")
            return row
        fields = dict(fields)
        if "zones" in fields:
            fields["zones_json"] = _json(fields.pop("zones"))
        if "layers" in fields:
            fields["layers_json"] = _json(fields.pop("layers"))
        if "gate" in fields:
            fields["gate_json"] = _json(fields.pop("gate"))
        if "live" in fields:
            fields["live"] = 1 if fields["live"] else 0
        if "resting" in fields:
            fields["resting"] = 1 if fields["resting"] else 0
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE machine_plans SET {sets} WHERE id=? AND user_id=?",
            tuple(fields.values()) + (int(plan_id), int(user_id)),
        )
        row = self.get_plan(user_id, plan_id)
        if not row:
            raise KeyError("plan not found")
        return row

    # ---- orders (simulated working layers) ----

    def list_orders(
        self, user_id: int, plan_id: Optional[int] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM machine_orders WHERE user_id=?"
        params: List[Any] = [int(user_id)]
        if plan_id is not None:
            sql += " AND plan_id=?"
            params.append(int(plan_id))
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY layer_idx ASC, id ASC"
        return self._exec(sql, tuple(params), fetch="all")

    def replace_working_orders(
        self, user_id: int, plan_id: int, layers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        now = time.time()
        self._exec(
            "UPDATE machine_orders SET status='cancelled', updated_at=? "
            "WHERE user_id=? AND plan_id=? AND status='working'",
            (now, int(user_id), int(plan_id)),
        )
        for layer in layers:
            self._exec(
                """
                INSERT INTO machine_orders (
                    user_id, plan_id, layer_idx, price, usd, qty, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'working', ?, ?)
                """,
                (
                    int(user_id),
                    int(plan_id),
                    int(layer.get("idx") or 0),
                    float(layer["price"]),
                    float(layer["usd"]),
                    layer.get("qty"),
                    now,
                    now,
                ),
            )
        return self.list_orders(user_id, plan_id, status="working")

    def cancel_working(self, user_id: int, plan_id: int) -> None:
        self._exec(
            "UPDATE machine_orders SET status='cancelled', updated_at=? "
            "WHERE user_id=? AND plan_id=? AND status='working'",
            (time.time(), int(user_id), int(plan_id)),
        )

    # ---- closes + KB ----

    def insert_close(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        cid = self._exec(
            """
            INSERT INTO machine_closes (
                user_id, plan_id, symbol, market, tf, reason, reds, volume,
                bounce_or_fail, process_ok, money_pnl, allocated_usd,
                payload_json, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(payload["plan_id"]),
                str(payload.get("symbol") or ""),
                str(payload.get("market") or ""),
                payload.get("tf"),
                str(payload.get("reason") or "close"),
                payload.get("reds"),
                payload.get("volume"),
                payload.get("bounce_or_fail"),
                1 if payload.get("process_ok") else 0,
                payload.get("money_pnl"),
                payload.get("allocated_usd"),
                _json(payload.get("payload")),
                now,
            ),
        )
        return self._exec(
            "SELECT * FROM machine_closes WHERE id=?",
            (int(cid),),
            fetch="one",
        )

    def insert_kb(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        kid = self._exec(
            """
            INSERT INTO machine_kb (
                user_id, close_id, plan_id, symbol, market, tf, reds, volume,
                bounce_or_fail, process_ok, money_pnl, habit_reds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(payload["close_id"]),
                int(payload["plan_id"]),
                str(payload.get("symbol") or ""),
                str(payload.get("market") or ""),
                payload.get("tf"),
                payload.get("reds"),
                payload.get("volume"),
                payload.get("bounce_or_fail"),
                1 if payload.get("process_ok") else 0,
                payload.get("money_pnl"),
                payload.get("habit_reds"),
                now,
            ),
        )
        return self._exec(
            "SELECT * FROM machine_kb WHERE id=?",
            (int(kid),),
            fetch="one",
        )

    def list_closes(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        return self._exec(
            "SELECT * FROM machine_closes WHERE user_id=? "
            "ORDER BY closed_at DESC, id DESC LIMIT ?",
            (int(user_id), int(limit)),
            fetch="all",
        )

    def list_kb(self, user_id: int, limit: int = 80) -> List[Dict[str, Any]]:
        return self._exec(
            "SELECT * FROM machine_kb WHERE user_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(user_id), int(limit)),
            fetch="all",
        )

    def habit_reds(self, user_id: int, symbol: str, market: str, tf: str) -> Optional[int]:
        row = self._exec(
            """
            SELECT habit_reds FROM machine_kb
            WHERE user_id=? AND symbol=? AND market=? AND tf=?
              AND habit_reds IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (int(user_id), str(symbol).upper(), str(market).lower(), str(tf)),
            fetch="one",
        )
        if not row or row.get("habit_reds") is None:
            return None
        return int(row["habit_reds"])

    def respected_scores(
        self, user_id: int, symbol: str, market: str
    ) -> Dict[str, float]:
        rows = self._exec(
            """
            SELECT tf, bounce_or_fail, process_ok FROM machine_kb
            WHERE user_id=? AND symbol=? AND market=? AND tf IS NOT NULL
            """,
            (int(user_id), str(symbol).upper(), str(market).lower()),
            fetch="all",
        )
        scores: Dict[str, float] = {}
        for r in rows:
            tf = str(r.get("tf") or "")
            if not tf:
                continue
            scores.setdefault(tf, 0.0)
            if r.get("bounce_or_fail") == "bounce":
                scores[tf] += 1.0
            elif r.get("bounce_or_fail") == "fail":
                scores[tf] -= 1.0
            if r.get("process_ok"):
                scores[tf] += 0.25
        return scores

    # ---- needs-you ----

    def add_need(
        self,
        user_id: int,
        kind: str,
        *,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        nid = self._exec(
            """
            INSERT INTO machine_needs_you (
                user_id, kind, status, symbol, market, payload_json, created_at
            ) VALUES (?, ?, 'open', ?, ?, ?, ?)
            """,
            (
                int(user_id),
                str(kind),
                str(symbol).upper() if symbol else None,
                str(market).lower() if market else None,
                _json(payload),
                time.time(),
            ),
        )
        return self.get_need(user_id, int(nid))

    def get_need(self, user_id: int, need_id: int) -> Dict[str, Any]:
        row = self._exec(
            "SELECT * FROM machine_needs_you WHERE user_id=? AND id=?",
            (int(user_id), int(need_id)),
            fetch="one",
        )
        if not row:
            raise KeyError("needs-you not found")
        return row

    def list_needs(self, user_id: int, status: str = "open") -> List[Dict[str, Any]]:
        return self._exec(
            "SELECT * FROM machine_needs_you WHERE user_id=? AND status=? "
            "ORDER BY id ASC",
            (int(user_id), status),
            fetch="all",
        )

    def resolve_need(
        self, user_id: int, need_id: int, status: str
    ) -> Dict[str, Any]:
        if status not in ("accepted", "rejected"):
            raise ValueError("status must be accepted or rejected")
        self._exec(
            "UPDATE machine_needs_you SET status=?, resolved_at=? "
            "WHERE user_id=? AND id=? AND status='open'",
            (status, time.time(), int(user_id), int(need_id)),
        )
        return self.get_need(user_id, need_id)

    def live_count(self, user_id: int) -> int:
        row = self._exec(
            "SELECT COUNT(*) AS c FROM machine_plans WHERE user_id=? AND live=1",
            (int(user_id),),
            fetch="one",
        )
        return int((row or {}).get("c") or 0)

    def live_allocated(self, user_id: int) -> float:
        row = self._exec(
            "SELECT COALESCE(SUM(allocated_usd),0) AS s "
            "FROM machine_plans WHERE user_id=? AND live=1",
            (int(user_id),),
            fetch="one",
        )
        return float((row or {}).get("s") or 0)

    def table_names(self) -> List[str]:
        rows = self._exec(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            fetch="all",
        )
        return [str(r["name"]) for r in rows]

    def machine_table_names(self) -> List[str]:
        return [t for t in self.table_names() if t in MACHINE_TABLES]


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def parse_json(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default

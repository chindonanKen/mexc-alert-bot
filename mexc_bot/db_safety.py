"""Additive-only SQLite schema safety.

Hard rule (owner 2026-08-11): deploy, rebuild, desk updates, and migrations
must never wipe or erase production data. Schema changes are **add-only**
unless a table rebuild is required — and rebuilds must copy every row and
abort if the copy would shrink the table.

Use these helpers for every new table/column. Prefer:

- ``CREATE TABLE IF NOT EXISTS``
- ``ALTER TABLE … ADD COLUMN`` via :func:`ensure_column`
- :func:`safe_rebuild_table` only when PK/shape cannot be altered in place

Never ``DROP TABLE`` a live data table without going through
:func:`safe_rebuild_table`. Never ``DELETE FROM`` in migration/init paths.
User-facing deletes (remove one alert, delete one lesson) stay in application
APIs — not migrations.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

logger = logging.getLogger(__name__)

# Core durable tables — never dropped empty; counts monitored at deploy.
PROTECTED_TABLES: frozenset[str] = frozenset(
    {
        "alerts",
        "mover_settings",
        "mover_watchlist",
        "mover_sets",
        "learning_events",
        "learning_labels",
        "learning_outcomes",
        "learning_lessons",
        "learning_pending_questions",
        "journal_trades",
        "journal_fills",
        "agent_setup_cases",
        "position_flags",
        "news_events",
        "delist_cache",
        "investigations",
        "belief_ticker",
        "belief_setup",
        "belief_updates",
        "agent_cases",
        "chart_profiles",
        "target_fire_log",
        "machine_plans",
        "machine_orders",
        "machine_closes",
        "machine_kb",
        "machine_needs_you",
    }
)

# Temp/rebuild names allowed to be dropped after a verified swap.
_TEMP_TABLE_RE = re.compile(r".+_(?:new|tmp|old|rebuild)$", re.I)

WATCHLIST_SNAPSHOT_NAME = "watchlist_snapshot.json"
SCHEMA_LOCK_NAME = "schema.lock"


def safety_dir(db_path: Path) -> Path:
    """Host-side `.safety/` next to the SQLite file (shared by bot + desk)."""
    return Path(db_path).resolve().parent / ".safety"


@contextmanager
def exclusive_schema_lock(
    db_path: Path, *, timeout_sec: float = 30.0, required: bool = False
) -> Iterator[None]:
    """Cross-process lock (bot + desk share the data bind-mount).

    Request-path init must never crash if ``.safety`` is root-owned or
    unwritable — that took Telegram down (PermissionError on schema.lock).
    Rebuild scripts may pass ``required=True``.
    """
    import fcntl

    candidates = [
        safety_dir(db_path) / SCHEMA_LOCK_NAME,
        Path(db_path).resolve().parent / ".schema.lock",
    ]
    fd = None
    lock_path: Optional[Path] = None
    last_err: Optional[BaseException] = None
    for cand in candidates:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            fd = open(cand, "a+", encoding="utf-8")
            lock_path = cand
            break
        except OSError as e:
            last_err = e
            logger.warning("schema lock path %s unusable: %s", cand, e)
            fd = None
    if fd is None or lock_path is None:
        msg = f"schema lock unavailable ({last_err})"
        if required:
            raise SchemaSafetyError(msg)
        logger.error("%s — continuing without cross-process lock", msg)
        yield
        return

    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    try:
        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    try:
                        fd.close()
                    except OSError:
                        pass
                    msg = f"schema lock timeout after {timeout_sec}s ({lock_path})"
                    if required:
                        raise SchemaSafetyError(msg)
                    logger.error("%s — continuing without lock", msg)
                    yield
                    return
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fd.close()
        except OSError:
            pass


def watchlist_pk_columns(conn: sqlite3.Connection) -> list[str]:
    if not table_exists(conn, "mover_watchlist"):
        return []
    return [
        str(r[1])
        for r in conn.execute("PRAGMA table_info(mover_watchlist)")
        if int(r[5] or 0) > 0
    ]


def watchlist_schema_is_final(conn: sqlite3.Connection) -> bool:
    """True when PK is (set_id, symbol, market) — the only live shape."""
    if watchlist_pk_columns(conn) == ["set_id", "symbol", "market"]:
        return True
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mover_watchlist'"
    ).fetchone()
    sql = ""
    if row is not None:
        sql = (row[0] if not isinstance(row, sqlite3.Row) else row["sql"]) or ""
    compact = "".join(str(sql).split())
    return "PRIMARYKEY(set_id,symbol,market)" in compact


def snapshot_watchlist_rows(conn: sqlite3.Connection) -> list[dict]:
    if not table_exists(conn, "mover_watchlist"):
        return []
    rows = conn.execute(
        "SELECT user_id, symbol, market, set_id FROM mover_watchlist "
        "ORDER BY user_id, market, symbol"
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            out.append(
                {
                    "user_id": r["user_id"],
                    "symbol": r["symbol"],
                    "market": r["market"],
                    "set_id": r["set_id"],
                }
            )
        else:
            out.append(
                {"user_id": r[0], "symbol": r[1], "market": r[2], "set_id": r[3]}
            )
    return out


def write_watchlist_snapshot(path: Path, rows: Sequence[dict]) -> None:
    """Atomic JSON write of the live coin list (quiet coins included)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "count": len(rows),
        "pid": os.getpid(),
        "rows": list(rows),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_watchlist_snapshot(path: Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("watchlist snapshot unreadable %s: %s", path, e)
        return []
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or "").strip()
        if not sym:
            continue
        out.append(
            {
                "user_id": r.get("user_id"),
                "symbol": sym.upper(),
                "market": str(r.get("market") or "futures").lower(),
                "set_id": r.get("set_id"),
            }
        )
    return out


class SchemaSafetyError(RuntimeError):
    """Raised when a migration would lose data or violate additive rules."""


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def row_count(conn: sqlite3.Connection, table: str) -> int:
    if not _is_safe_ident(table):
        raise SchemaSafetyError(f"unsafe table name: {table!r}")
    try:
        return int(conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return 0


def _is_safe_ident(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))


def ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
) -> bool:
    """ADD COLUMN if missing. Never drops or renames columns. Returns True if added."""
    if not _is_safe_ident(table) or not _is_safe_ident(column):
        raise SchemaSafetyError(f"unsafe identifier: {table}.{column}")
    # col_type is constrained (no multi-statement)
    if not re.fullmatch(r"[A-Za-z0-9_(),.\s]+", col_type or ""):
        raise SchemaSafetyError(f"unsafe column type: {col_type!r}")
    cols = {
        str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if column in cols:
        return False
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_type}')
    logger.info("schema: added column %s.%s %s", table, column, col_type)
    return True


def create_table_if_not_exists(conn: sqlite3.Connection, ddl: str) -> None:
    """Run CREATE TABLE IF NOT EXISTS … only. Rejects DROP/DELETE in the DDL."""
    compact = " ".join(ddl.split()).upper()
    if not compact.startswith("CREATE TABLE"):
        raise SchemaSafetyError("create_table_if_not_exists: DDL must be CREATE TABLE")
    if "IF NOT EXISTS" not in compact:
        raise SchemaSafetyError("create_table_if_not_exists: require IF NOT EXISTS")
    for banned in ("DROP ", "DELETE ", "TRUNCATE ", "ALTER TABLE"):
        if banned in compact and banned != "ALTER TABLE":
            # DROP/DELETE never in create path
            if banned.strip() in ("DROP", "DELETE", "TRUNCATE"):
                raise SchemaSafetyError(f"banned keyword in DDL: {banned}")
    if re.search(r"\bDROP\b", compact) or re.search(r"\bDELETE\b", compact):
        raise SchemaSafetyError("DDL must not contain DROP or DELETE")
    conn.execute(ddl)


def is_allowed_temp_table(name: str) -> bool:
    return bool(_TEMP_TABLE_RE.match(name or ""))


def assert_no_data_loss(
    before: int,
    after: int,
    *,
    table: str,
    context: str = "migration",
) -> None:
    """Abort if a rebuild would shrink the table."""
    if before > 0 and after == 0:
        raise SchemaSafetyError(
            f"{context}: refusing to wipe {table} ({before} → 0 rows)"
        )
    if after < before:
        raise SchemaSafetyError(
            f"{context}: refusing data loss on {table} ({before} → {after} rows)"
        )


def safe_rebuild_table(
    conn: sqlite3.Connection,
    *,
    table: str,
    create_new_ddl: str,
    copy_sql: str,
    after_swap: Optional[Callable[[sqlite3.Connection], None]] = None,
    temp_suffix: str = "_new",
    lock_path: Optional[Path] = None,
) -> bool:
    """Rebuild ``table`` via temp copy; never DROP live data unless copy is complete.

    Returns True if a rebuild ran, False if skipped (already correct shape).

    **Not for request-path init.** Bot/desk ``MoverStore()`` must never call this.
    Use an explicit migrate script, and pass ``lock_path`` so two processes cannot
    DROP+CREATE-empty the live table.

    Steps (one exclusive transaction):
      1. CREATE temp table (must not be the live name)
      2. Run copy_sql (INSERT … SELECT into temp)
      3. Verify row counts: after >= before; else abort and DROP temp only
      4. DROP live + RENAME temp → live (only after verification)

    ``create_new_ddl`` must create ``{table}{temp_suffix}`` only.
    """
    if lock_path is not None:
        with exclusive_schema_lock(Path(lock_path), required=True):
            return _safe_rebuild_table_unlocked(
                conn,
                table=table,
                create_new_ddl=create_new_ddl,
                copy_sql=copy_sql,
                after_swap=after_swap,
                temp_suffix=temp_suffix,
            )
    return _safe_rebuild_table_unlocked(
        conn,
        table=table,
        create_new_ddl=create_new_ddl,
        copy_sql=copy_sql,
        after_swap=after_swap,
        temp_suffix=temp_suffix,
    )


def _safe_rebuild_table_unlocked(
    conn: sqlite3.Connection,
    *,
    table: str,
    create_new_ddl: str,
    copy_sql: str,
    after_swap: Optional[Callable[[sqlite3.Connection], None]],
    temp_suffix: str,
) -> bool:
    if not _is_safe_ident(table):
        raise SchemaSafetyError(f"unsafe table name: {table!r}")
    temp = f"{table}{temp_suffix}"
    if not is_allowed_temp_table(temp):
        raise SchemaSafetyError(f"temp table must match *_new/*_tmp pattern: {temp}")
    if table in create_new_ddl and temp not in create_new_ddl.replace(" ", ""):
        if temp not in create_new_ddl:
            raise SchemaSafetyError("create_new_ddl must target the temp table name")

    if not table_exists(conn, table):
        # Missing live table is a wipe-in-progress, not a reason to create empty.
        logger.error(
            "safe_rebuild_table: refusing to create empty %s (table missing)", table
        )
        return False

    before = row_count(conn, table)
    if before == 0 and table in PROTECTED_TABLES:
        # Empty protected table + rebuild is how a race finished at 0 rows.
        raise SchemaSafetyError(
            f"safe_rebuild_table: refusing rebuild of empty protected table {table}"
        )

    # Exclusive write lock so a second process cannot CREATE IF NOT EXISTS
    # an empty shell in the DROP→RENAME gap.
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{temp}"')
        conn.execute(create_new_ddl)
        conn.execute(copy_sql)
        after = row_count(conn, temp)
        assert_no_data_loss(before, after, table=table, context="safe_rebuild_table")
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{temp}" RENAME TO "{table}"')
        if after_swap:
            after_swap(conn)
        conn.execute("COMMIT")
    except Exception:
        logger.error(
            "safe_rebuild_table aborted for %s (live table rolled back if possible)",
            table,
        )
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            try:
                conn.execute(f'DROP TABLE IF EXISTS "{temp}"')
            except sqlite3.Error:
                pass
        raise
    logger.info(
        "safe_rebuild_table: %s rebuilt (%s rows preserved)", table, after
    )
    return True


def snapshot_counts(
    conn: sqlite3.Connection,
    tables: Optional[Iterable[str]] = None,
) -> dict[str, int]:
    """Row counts for protected (or given) tables that exist."""
    out: dict[str, int] = {}
    names = list(tables) if tables is not None else sorted(PROTECTED_TABLES)
    for name in names:
        if not _is_safe_ident(name):
            continue
        if table_exists(conn, name):
            out[name] = row_count(conn, name)
    return out


def compare_snapshots(
    before: dict[str, int],
    after: dict[str, int],
    *,
    allow_growth: bool = True,
) -> list[str]:
    """Return human-readable violations if any protected table lost rows."""
    problems: list[str] = []
    for name, b in before.items():
        if b <= 0:
            continue
        a = after.get(name)
        if a is None:
            problems.append(f"{name}: missing after migrate (had {b} rows)")
        elif a < b:
            problems.append(f"{name}: row loss {b} → {a}")
        elif not allow_growth and a != b:
            problems.append(f"{name}: unexpected change {b} → {a}")
    return problems

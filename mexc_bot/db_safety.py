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

import logging
import re
import sqlite3
from typing import Callable, Iterable, Optional, Sequence

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
    }
)

# Temp/rebuild names allowed to be dropped after a verified swap.
_TEMP_TABLE_RE = re.compile(r".+_(?:new|tmp|old|rebuild)$", re.I)


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
) -> bool:
    """Rebuild ``table`` via temp copy; never DROP live data unless copy is complete.

    Returns True if a rebuild ran, False if skipped (already correct shape).

    Steps:
      1. CREATE temp table (must not be the live name)
      2. Run copy_sql (INSERT … SELECT into temp)
      3. Verify row counts: after >= before; else abort and DROP temp only
      4. DROP live + RENAME temp → live (only after verification)

    ``create_new_ddl`` must create ``{table}{temp_suffix}`` only.
    """
    if not _is_safe_ident(table):
        raise SchemaSafetyError(f"unsafe table name: {table!r}")
    temp = f"{table}{temp_suffix}"
    if not is_allowed_temp_table(temp):
        raise SchemaSafetyError(f"temp table must match *_new/*_tmp pattern: {temp}")
    if table in create_new_ddl and temp not in create_new_ddl.replace(" ", ""):
        # soft check — require temp name in DDL
        if temp not in create_new_ddl:
            raise SchemaSafetyError("create_new_ddl must target the temp table name")

    if not table_exists(conn, table):
        # Nothing to rebuild; caller should CREATE IF NOT EXISTS the final form
        return False

    before = row_count(conn, table)
    conn.execute(f'DROP TABLE IF EXISTS "{temp}"')
    conn.execute(create_new_ddl)
    conn.execute(copy_sql)
    after = row_count(conn, temp)
    try:
        assert_no_data_loss(before, after, table=table, context="safe_rebuild_table")
    except SchemaSafetyError:
        logger.error(
            "safe_rebuild_table aborted for %s: %s → %s rows (temp dropped)",
            table,
            before,
            after,
        )
        conn.execute(f'DROP TABLE IF EXISTS "{temp}"')
        raise

    # Verified: swap
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{temp}" RENAME TO "{table}"')
    if after_swap:
        after_swap(conn)
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

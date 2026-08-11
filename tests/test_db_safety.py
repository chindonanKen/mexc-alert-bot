"""DB durability: additive schema only, rebuilds abort on row loss."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.db_safety import (
    SchemaSafetyError,
    assert_no_data_loss,
    compare_snapshots,
    ensure_column,
    safe_rebuild_table,
    snapshot_counts,
)
from mexc_bot.movers.storage import MoverStore


class TestDbSafetyHelpers(unittest.TestCase):
    def test_ensure_column_additive(self) -> None:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        self.assertTrue(ensure_column(con, "t", "x", "TEXT"))
        self.assertFalse(ensure_column(con, "t", "x", "TEXT"))
        cols = {r[1] for r in con.execute("PRAGMA table_info(t)")}
        self.assertIn("x", cols)

    def test_assert_no_data_loss(self) -> None:
        assert_no_data_loss(0, 0, table="t")
        assert_no_data_loss(5, 5, table="t")
        assert_no_data_loss(5, 7, table="t")
        with self.assertRaises(SchemaSafetyError):
            assert_no_data_loss(5, 0, table="t")
        with self.assertRaises(SchemaSafetyError):
            assert_no_data_loss(5, 4, table="t")

    def test_safe_rebuild_preserves_rows(self) -> None:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        con.executemany(
            "INSERT INTO items (id, name) VALUES (?, ?)",
            [(1, "a"), (2, "b"), (3, "c")],
        )
        safe_rebuild_table(
            con,
            table="items",
            create_new_ddl="""
            CREATE TABLE items_new (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                extra TEXT
            )
            """,
            copy_sql="INSERT INTO items_new (id, name) SELECT id, name FROM items",
        )
        n = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self.assertEqual(n, 3)
        cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
        self.assertIn("extra", cols)

    def test_safe_rebuild_aborts_on_empty_copy(self) -> None:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO items VALUES (1, 'a')")
        with self.assertRaises(SchemaSafetyError):
            safe_rebuild_table(
                con,
                table="items",
                create_new_ddl="CREATE TABLE items_new (id INTEGER PRIMARY KEY, name TEXT)",
                copy_sql="INSERT INTO items_new SELECT * FROM items WHERE 0",
            )
        # Live table intact
        self.assertEqual(con.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)

    def test_snapshot_compare(self) -> None:
        problems = compare_snapshots(
            {"alerts": 10, "mover_watchlist": 5},
            {"alerts": 10, "mover_watchlist": 0},
        )
        self.assertTrue(any("mover_watchlist" in p for p in problems))
        self.assertEqual(
            compare_snapshots({"alerts": 3}, {"alerts": 3, "x": 1}),
            [],
        )


class TestMoverWatchlistMigrationNoWipe(unittest.TestCase):
    def test_legacy_pk_rebuild_keeps_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            con = sqlite3.connect(db)
            con.execute(
                """
                CREATE TABLE mover_settings (
                    user_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    threshold_percent REAL NOT NULL,
                    lookback_seconds INTEGER NOT NULL
                )
                """
            )
            con.execute(
                "INSERT INTO mover_settings VALUES (1, 1, 7.0, 900)"
            )
            # Old PK without set_id in PK
            con.execute(
                """
                CREATE TABLE mover_watchlist (
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'futures',
                    PRIMARY KEY (user_id, symbol, market)
                )
                """
            )
            for sym in ("BLUAI_USDT", "BTC_USDT", "ETH_USDT"):
                con.execute(
                    "INSERT INTO mover_watchlist (user_id, symbol, market) VALUES (1, ?, 'futures')",
                    (sym,),
                )
            con.commit()
            con.close()

            store = MoverStore(db)
            wl = store.get_watchlist(1)
            self.assertGreaterEqual(len(wl), 3)
            symbols = {r["symbol"] for r in wl}
            self.assertIn("BLUAI_USDT", symbols)


class TestStaticGuardScript(unittest.TestCase):
    def test_static_scan_passes_on_repo(self) -> None:
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "scripts/db_safety_check.py"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            self.fail(r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

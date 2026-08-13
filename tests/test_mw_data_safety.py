#!/usr/bin/env python3
"""Hard safety: mover watchlist must not wipe on bad /mw usage."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.bot import _parse_mw_token, _strip_mw_token
from mexc_bot.db_safety import read_watchlist_snapshot, write_watchlist_snapshot
from mexc_bot.movers.storage import MoverStore


class TestMwTokenParse(unittest.TestCase):
    def test_strip_quotes(self) -> None:
        self.assertEqual(_strip_mw_token("'BLUAI'"), "BLUAI")
        self.assertEqual(_strip_mw_token('"BTC"'), "BTC")

    def test_parse_quoted(self) -> None:
        body, mkt = _parse_mw_token("'BLUAI'", default_market="futures")
        self.assertEqual(body, "BLUAI")
        self.assertEqual(mkt, "futures")


class TestWatchlistNoWipe(unittest.TestCase):
    def test_set_watchlist_refuses_empty_replace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 1
            store.set_watchlist(
                u,
                [
                    {"symbol": "BTC_USDT", "market": "futures"},
                    {"symbol": "ETH_USDT", "market": "futures"},
                    {"symbol": "SOL_USDT", "market": "futures"},
                ],
            )
            assert len(store.get_watchlist(u)) == 3
            with self.assertRaises(ValueError) as ctx:
                store.set_watchlist(u, [])
            self.assertIn("refusing", str(ctx.exception).lower())
            self.assertEqual(len(store.get_watchlist(u)), 3)

    def test_set_watchlist_force_empty_for_clear(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 2
            store.set_watchlist(u, [{"symbol": "BTC_USDT", "market": "futures"}])
            n = store.set_watchlist(u, [], force_empty=True)
            self.assertEqual(n, 0)

    def test_add_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 3
            store.set_watchlist(
                u,
                [
                    {"symbol": "BTC_USDT", "market": "futures"},
                    {"symbol": "ETH_USDT", "market": "futures"},
                ],
            )
            store.add_watchlist(u, "SOL_USDT", "futures")
            syms = {r["symbol"] for r in store.get_watchlist(u)}
            self.assertEqual(syms, {"BTC_USDT", "ETH_USDT", "SOL_USDT"})

    def test_failed_resolve_pattern_would_not_empty_via_set(self) -> None:
        """Simulates bare /mw BADCOIN that resolved to zero items — must not wipe."""
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 4
            store.set_watchlist(
                u,
                [{"symbol": "BLUAI_USDT", "market": "futures"}] * 1
                + [{"symbol": f"COIN{i}_USDT", "market": "futures"} for i in range(5)],
            )
            before = len(store.get_watchlist(u))
            self.assertGreater(before, 1)
            # old bug: set_watchlist(u, []) after all resolve failed
            with self.assertRaises(ValueError):
                store.set_watchlist(u, [])
            self.assertEqual(len(store.get_watchlist(u)), before)


class TestWatchlistPkMigrateIdempotent(unittest.TestCase):
    def test_already_new_pk_does_not_rebuild(self) -> None:
        """Regression: space-stripped SQL never matched 'PRIMARY KEY (…)' so
        every desk GET rebuilt mover_watchlist and raced it empty."""
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.db"
            store = MoverStore(path)
            u = 9
            store.set_watchlist(
                u,
                [
                    {"symbol": "AAA_USDT", "market": "futures"},
                    {"symbol": "BBB_USDT", "market": "futures"},
                ],
            )
            self.assertEqual(len(store.get_watchlist(u)), 2)
            store2 = MoverStore(path)
            self.assertEqual(len(store2.get_watchlist(u)), 2)
            store2._migrate_watchlist_pk(store2._get_conn())
            self.assertEqual(len(store2.get_watchlist(u)), 2)
            sql = store2._get_conn().execute(
                "SELECT sql FROM sqlite_master WHERE name='mover_watchlist'"
            ).fetchone()["sql"]
            compact = "".join(sql.split())
            self.assertIn("PRIMARYKEY(set_id,symbol,market)", compact)


class TestWatchlistSnapshotAndInitFreeze(unittest.TestCase):
    def test_double_init_never_changes_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.db"
            store = MoverStore(path)
            u = 11
            coins = [{"symbol": f"C{i}_USDT", "market": "futures"} for i in range(20)]
            store.set_watchlist(u, coins)
            n = len(store.get_watchlist(u))
            self.assertEqual(n, 20)
            for _ in range(5):
                other = MoverStore(path)
                self.assertEqual(len(other.get_watchlist(u)), 20)

    def test_wipe_recovers_from_snapshot(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.db"
            store = MoverStore(path)
            u = 12
            store.set_watchlist(
                u,
                [
                    {"symbol": "PI_USDT", "market": "futures"},
                    {"symbol": "OXTUSDT", "market": "spot"},
                    {"symbol": "GENIUS_USDT", "market": "futures"},
                ],
            )
            snap = path.parent / ".safety" / "watchlist_snapshot.json"
            self.assertTrue(snap.is_file())
            self.assertGreaterEqual(len(read_watchlist_snapshot(snap)), 3)
            con = sqlite3.connect(path)
            con.execute("DELETE FROM mover_watchlist")
            con.commit()
            con.close()
            # Simulate bot+desk restart after the PK-race wipe
            revived = MoverStore(path)
            symbols = {r["symbol"] for r in revived.get_watchlist(u)}
            self.assertEqual(symbols, {"PI_USDT", "OXTUSDT", "GENIUS_USDT"})

    def test_user_clear_does_not_resurrect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.db"
            store = MoverStore(path)
            u = 13
            store.set_watchlist(u, [{"symbol": "ACE_USDT", "market": "futures"}])
            store.clear_watchlist(u)
            self.assertEqual(store.get_watchlist(u), [])
            again = MoverStore(path)
            self.assertEqual(again.get_watchlist(u), [])

    def test_init_never_creates_empty_over_missing_table_with_snapshot(self) -> None:
        """If someone drops the live table, snapshot coins must come back."""
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.db"
            store = MoverStore(path)
            u = 14
            store.set_watchlist(u, [{"symbol": "BLUAI_USDT", "market": "futures"}])
            con = sqlite3.connect(path)
            con.execute("DROP TABLE mover_watchlist")
            con.commit()
            con.close()
            revived = MoverStore(path)
            symbols = {r["symbol"] for r in revived.get_watchlist(u)}
            self.assertIn("BLUAI_USDT", symbols)

    def test_snapshot_roundtrip_helper(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wl.json"
            rows = [
                {
                    "user_id": 1,
                    "symbol": "AAA_USDT",
                    "market": "futures",
                    "set_id": 1,
                }
            ]
            write_watchlist_snapshot(p, rows)
            back = read_watchlist_snapshot(p)
            self.assertEqual(back[0]["symbol"], "AAA_USDT")


class TestBotMwHandlerSourceGuard(unittest.TestCase):
    """Static: bare symbols must not call set_watchlist; clear needs confirm."""

    def test_bot_mw_source_safety(self) -> None:
        text = Path("mexc_bot/bot.py").read_text(encoding="utf-8")
        # Extract cmd_mover_watch roughly
        start = text.find('commands=["mw"')
        self.assertGreater(start, 0)
        chunk = text[start : start + 12000]
        self.assertIn("clear confirm", chunk)
        self.assertIn("replace", chunk)
        self.assertIn("do_add", chunk)
        # Destructive set_watchlist only inside replace/set/only path with empty abort
        self.assertIn("Replace aborted", chunk)
        self.assertIn("List unchanged for failed names", chunk)


if __name__ == "__main__":
    unittest.main()

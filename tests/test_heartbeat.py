#!/usr/bin/env python3
"""Heartbeat must be fail-soft and distinguish polling from 'db file exists'."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.heartbeat import check_alive, heartbeat_path, read_heartbeat, touch_heartbeat


class TestHeartbeat(unittest.TestCase):
    def test_roundtrip_polling(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            touch_heartbeat(td, polling=True, monitor=True)
            self.assertTrue(check_alive(td, max_age_sec=10))
            hb = read_heartbeat(td)
            self.assertTrue(hb and hb.get("polling"))

    def test_stale_is_dead(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            touch_heartbeat(td, polling=True)
            p = heartbeat_path(td)
            import json

            d = json.loads(p.read_text(encoding="utf-8"))
            d["ts"] = time.time() - 400
            p.write_text(json.dumps(d), encoding="utf-8")
            self.assertFalse(check_alive(td, max_age_sec=90))

    def test_monitor_without_polling_is_dead(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            touch_heartbeat(td, monitor=True)
            self.assertFalse(check_alive(td, require_polling=True))
            self.assertTrue(check_alive(td, require_polling=False))

    def test_unwritable_dir_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.chmod(td, stat.S_IRUSR | stat.S_IXUSR)
            try:
                touch_heartbeat(td, polling=True)
            finally:
                os.chmod(td, stat.S_IRWXU)
            self.assertFalse(check_alive(td))


if __name__ == "__main__":
    unittest.main()

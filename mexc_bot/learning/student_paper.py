"""Unattended student paper book — fill on tag + this-chart habit.

Staff-only. Never a live exchange order. Master recuts in the morning.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .student_decide import (
    DEFAULT_TF,
    TZ_NAME,
    decide_book,
    fmt_px,
    should_paper_fill,
)

logger = logging.getLogger(__name__)

FetchBars = Callable[[str, str, str, int], List[dict]]
Notifier = Callable[..., None]


def _copy_key(bottom: Any) -> str:
    try:
        return f"{float(bottom):.8f}"
    except (TypeError, ValueError):
        return ""


def entry_notice_text(row: Dict[str, Any]) -> str:
    """Plain text for Telegram + desk. No live-order language."""
    sym = row.get("symbol") or "—"
    mkt = row.get("market") or ""
    tf = row.get("tf") or DEFAULT_TF
    copy = row.get("copy_text") or ""
    tag = row.get("tag") or "tagged"
    habit_reds = row.get("habit_reds")
    live_reds = row.get("live_reds")
    habit_vol = row.get("habit_vol") or "—"
    entry = fmt_px(row.get("entry_px"))
    return (
        f"Student entered (paper)\n"
        f"{sym} {mkt} {tf}\n"
        f"{copy} · {tag} @ {entry}\n"
        f"habit {habit_reds if habit_reds is not None else '—'} reds / {habit_vol} "
        f"· live {live_reds if live_reds is not None else '—'}\n"
        f"No live order. Recut in the morning."
    )


class StudentPaperBook:
    """Additive paper rows. Separate from journal money truth."""

    def __init__(self, event_store):
        self.store = event_store
        self._ensure()

    def _ensure(self) -> None:
        with self.store._lock:
            conn = self.store._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS student_paper_book (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    tf TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    entry_px REAL NOT NULL,
                    copy_top REAL,
                    copy_bottom REAL,
                    copy_text TEXT,
                    tag TEXT,
                    habit_reds INTEGER,
                    live_reds INTEGER,
                    habit_vol TEXT,
                    live_vol TEXT,
                    decide_json TEXT,
                    note TEXT,
                    opened_at REAL NOT NULL,
                    notified_at REAL,
                    recut_at REAL,
                    live_order INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_paper_user_open "
                "ON student_paper_book (user_id, status, opened_at DESC)"
            )

    def _row(self, raw) -> Dict[str, Any]:
        d = dict(raw)
        d["live_order"] = False
        d["live_orders"] = False
        return d

    def open_on_tag(
        self, user_id: int, decide: Dict[str, Any], *, now: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        if not should_paper_fill(decide):
            return None
        uid = int(user_id)
        if uid <= 0:
            return None
        copy = decide.get("live_copy") or {}
        bottom = copy.get("bottom")
        key = _copy_key(bottom)
        if not key:
            return None
        habit = decide.get("path_habit") or {}
        streak = decide.get("live_streak") or {}
        live_vol = decide.get("live_vol") or {}
        ts = float(now if now is not None else time.time())
        with self.store._lock:
            conn = self.store._get_conn()
            exists = conn.execute(
                """
                SELECT id FROM student_paper_book
                WHERE user_id=? AND symbol=? AND market=? AND tf=? AND status='open'
                  AND printf('%.8f', copy_bottom)=?
                LIMIT 1
                """,
                (
                    uid,
                    str(decide.get("symbol") or ""),
                    str(decide.get("market") or "futures"),
                    str(decide.get("tf") or DEFAULT_TF),
                    key,
                ),
            ).fetchone()
            if exists:
                return None
            cur = conn.execute(
                """
                INSERT INTO student_paper_book (
                    user_id, symbol, market, tf, status, entry_px,
                    copy_top, copy_bottom, copy_text, tag,
                    habit_reds, live_reds, habit_vol, live_vol,
                    decide_json, note, opened_at, notified_at, recut_at, live_order
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    uid,
                    str(decide.get("symbol") or ""),
                    str(decide.get("market") or "futures"),
                    str(decide.get("tf") or DEFAULT_TF),
                    "open",
                    float(bottom),
                    copy.get("top"),
                    float(bottom),
                    copy.get("text") or "",
                    decide.get("tag"),
                    habit.get("reds"),
                    streak.get("reds"),
                    habit.get("vol"),
                    live_vol.get("flag"),
                    json.dumps(decide),
                    "unattended tag · this-chart habit",
                    ts,
                    None,
                    None,
                ),
            )
            rid = int(cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM student_paper_book WHERE id=? AND user_id=?",
                (rid, uid),
            ).fetchone()
        return self._row(row) if row else None

    def mark_notified(self, user_id: int, paper_id: int, *, now: Optional[float] = None) -> None:
        ts = float(now if now is not None else time.time())
        with self.store._lock:
            self.store._get_conn().execute(
                "UPDATE student_paper_book SET notified_at=? WHERE id=? AND user_id=?",
                (ts, int(paper_id), int(user_id)),
            )

    def recut(self, user_id: int, paper_id: int, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        ts = float(now if now is not None else time.time())
        with self.store._lock:
            conn = self.store._get_conn()
            conn.execute(
                """
                UPDATE student_paper_book
                SET status='recut', recut_at=?
                WHERE id=? AND user_id=? AND status='open'
                """,
                (ts, int(paper_id), int(user_id)),
            )
            row = conn.execute(
                "SELECT * FROM student_paper_book WHERE id=? AND user_id=?",
                (int(paper_id), int(user_id)),
            ).fetchone()
        return self._row(row) if row else None

    def list_open(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self.store._lock:
            rows = self.store._get_conn().execute(
                """
                SELECT * FROM student_paper_book
                WHERE user_id=? AND status='open'
                ORDER BY opened_at DESC LIMIT ?
                """,
                (int(user_id), int(limit)),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_recent(self, user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
        with self.store._lock:
            rows = self.store._get_conn().execute(
                """
                SELECT * FROM student_paper_book
                WHERE user_id=?
                ORDER BY opened_at DESC LIMIT ?
                """,
                (int(user_id), int(limit)),
            ).fetchall()
        return [self._row(r) for r in rows]


def notify_student_entered(
    row: Dict[str, Any],
    *,
    user_id: int,
    notifier: Optional[Notifier],
    book: Optional[StudentPaperBook] = None,
) -> str:
    text = entry_notice_text(row)
    if notifier:
        try:
            notifier(int(user_id), text, parse_mode=None)
            if book and row.get("id"):
                book.mark_notified(user_id, int(row["id"]))
        except Exception as e:
            logger.warning("student paper notify failed: %s", e)
    return text


def watch_once(
    book: StudentPaperBook,
    user_id: int,
    *,
    names: Optional[Sequence[Dict[str, str]]] = None,
    tf: str = DEFAULT_TF,
    fetch_bars: Optional[FetchBars] = None,
    notifier: Optional[Notifier] = None,
) -> Dict[str, Any]:
    """One unattended pass: decide book → paper on tag+habit → notify."""
    walked = decide_book(
        names,
        tf=tf,
        fetch_bars=fetch_bars,
        user_id=user_id,
        walk=True,
    )
    filled: List[Dict[str, Any]] = []
    notices: List[str] = []
    for d in walked.get("decides") or []:
        row = book.open_on_tag(user_id, d)
        if not row:
            continue
        text = notify_student_entered(
            row, user_id=user_id, notifier=notifier, book=book
        )
        filled.append(row)
        notices.append(text)
    return {
        "ok": True,
        "live_orders": False,
        "tz": TZ_NAME,
        "tf": tf,
        "n_decides": walked.get("n") or 0,
        "n_filled": len(filled),
        "filled": filled,
        "notices": notices,
        "decides": walked.get("decides") or [],
    }


class StudentWatchPoller:
    """Background unattended decide. Paper only."""

    def __init__(
        self,
        event_store,
        user_id: int,
        *,
        notifier: Optional[Notifier] = None,
        fetch_bars: Optional[FetchBars] = None,
        names_fn: Optional[Callable[[], List[Dict[str, str]]]] = None,
        poll_seconds: float = 90.0,
        tf: str = DEFAULT_TF,
    ):
        self.book = StudentPaperBook(event_store)
        self.user_id = int(user_id)
        self.notifier = notifier
        self.fetch_bars = fetch_bars
        self.names_fn = names_fn
        self.poll_seconds = max(30.0, float(poll_seconds))
        self.tf = tf
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Dict[str, Any] = {}

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "user_id": self.user_id,
            "poll_seconds": self.poll_seconds,
            "live_orders": False,
            "last_n_filled": (self._last or {}).get("n_filled"),
        }

    def start(self) -> None:
        if self.user_id <= 0:
            logger.warning("StudentWatchPoller not started (no user_id)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="student-watch", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info(
            "Student watch started user=%s poll=%ss paper_only",
            self.user_id,
            self.poll_seconds,
        )
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                logger.exception("student watch error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def run_once(self) -> Dict[str, Any]:
        names = None
        if self.names_fn:
            try:
                names = list(self.names_fn() or [])
            except Exception:
                names = []
        self._last = watch_once(
            self.book,
            self.user_id,
            names=names,
            tf=self.tf,
            fetch_bars=self.fetch_bars,
            notifier=self.notifier,
        )
        return self._last

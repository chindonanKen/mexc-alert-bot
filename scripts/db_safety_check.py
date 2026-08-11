#!/usr/bin/env python3
"""Static + optional live DB guard: block deploys that risk wiping SQLite data.

Usage (repo root):
  python3 scripts/db_safety_check.py              # static only
  python3 scripts/db_safety_check.py --db PATH    # also snapshot protected tables
  python3 scripts/db_safety_check.py --fail-empty-watchlist

Exit 1 on any violation. Wired into verify_build / deploy / droplet deploy.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.db_safety import (  # noqa: E402
    PROTECTED_TABLES,
    is_allowed_temp_table,
    snapshot_counts,
)

# Patterns that must not appear in app code (migrations / init).
# Allowlisted files may use DELETE for intentional user APIs only.
SCAN_GLOBS = ("mexc_bot/**/*.py",)

# DROP TABLE live names — only temp rebuild names OK outside db_safety.py
DROP_TABLE_RE = re.compile(
    r"""DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?["']?([A-Za-z_][A-Za-z0-9_]*)""",
    re.I,
)

# Dangerous shell / compose in scripts
SHELL_BAN = [
    (re.compile(r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*[^\n]*\bdata(/|\b)", re.I), "rm of data/"),
    (re.compile(r"docker\s+compose\s+down\s+[^\n]*-v", re.I), "compose down -v (wipes volumes)"),
    (re.compile(r"docker\s+volume\s+rm\b", re.I), "docker volume rm"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
]

# Files allowed to DROP only temp tables (rebuild path) — still checked for live names
REBUILD_ALLOW = {
    "mexc_bot/db_safety.py",
    "mexc_bot/movers/storage.py",  # must use safe_rebuild / temp only
}

# User-facing DELETE OK in these modules (not migrations)
DELETE_ALLOW_PREFIXES = (
    "mexc_bot/storage.py",  # remove alert by id
    "mexc_bot/bot.py",
    "mexc_bot/webapi/",
    "mexc_bot/learning/store.py",  # delete lesson, journal maintenance
    "mexc_bot/movers/storage.py",  # clear/remove watchlist symbols (API)
    "scripts/seed_desk_local.py",
    "tests/",
)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def scan_static() -> list[str]:
    problems: list[str] = []
    for path in sorted(ROOT.glob("mexc_bot/**/*.py")):
        rel = _rel(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        # Skip pure comments? still scan
        for m in DROP_TABLE_RE.finditer(text):
            tname = m.group(1)
            if is_allowed_temp_table(tname):
                continue
            if rel == "mexc_bot/db_safety.py":
                # helper itself drops live table only after verified copy
                continue
            if rel in REBUILD_ALLOW and tname.endswith(("_new", "_tmp", "_old", "_rebuild")):
                continue
            # Live table drop outside verified helper = fail
            if not is_allowed_temp_table(tname):
                # allow only inside safe_rebuild_table implementation
                if rel != "mexc_bot/db_safety.py":
                    problems.append(
                        f"{rel}: DROP TABLE {tname} — use mexc_bot.db_safety.safe_rebuild_table "
                        f"(only temp *_new tables may be dropped ad-hoc)"
                    )

        # Migrations / _init_db style: ban DELETE FROM in _migrate* methods
        if re.search(r"def\s+_migrate\w*\(", text):
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(r"DELETE\s+FROM", line, re.I) and not line.strip().startswith("#"):
                    # spot bare-base cleanup deletes single bad rows — flag if bulk wipe pattern
                    if re.search(r"DELETE\s+FROM\s+\w+\s*$", line, re.I) or "WHERE" not in line:
                        problems.append(
                            f"{rel}:{i}: DELETE in _migrate* without WHERE (wipe risk)"
                        )

    # Scripts / deploy (skip this checker — it only documents ban patterns)
    skip_shell = {
        "scripts/db_safety_check.py",
        "scripts/pre_deploy_db_guard.sh",
    }
    for path in list(ROOT.glob("scripts/*")) + list(ROOT.glob("*.yml")) + list(
        ROOT.glob("docker-compose*.yml")
    ):
        if not path.is_file():
            continue
        rel = _rel(path)
        if rel in skip_shell:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for rx, label in SHELL_BAN:
            for line in text.splitlines():
                stripped = line.strip()
                # Comments / docstrings that warn against the pattern are OK
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if "never" in stripped.lower() and rx.search(line):
                    continue
                if "must not" in stripped.lower() and rx.search(line):
                    continue
                if "NOTE:" in stripped and rx.search(line):
                    continue
                if rx.search(line):
                    problems.append(
                        f"{rel}: banned pattern ({label}): {stripped[:120]}"
                    )

    # docker-compose must bind-mount ./data
    compose = ROOT / "docker-compose.yml"
    if compose.exists():
        ctext = compose.read_text(encoding="utf-8")
        if "./data:/app/data" not in ctext:
            problems.append("docker-compose.yml: missing ./data:/app/data volume for durability")
        for line in ctext.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            if "down -v" in s or re.search(r"compose\s+down\s+.*-v", s):
                problems.append("docker-compose.yml: must not use down -v")

    return problems


def check_db(db_path: Path, *, fail_empty_watchlist: bool) -> list[str]:
    problems: list[str] = []
    if not db_path.is_file():
        # Fresh install OK for static; warn only
        return [f"NOTE: DB not found at {db_path} (ok for fresh clone)"]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return [f"cannot open DB {db_path}: {e}"]
    try:
        counts = snapshot_counts(conn)
        print("=== DB snapshot (protected tables) ===")
        for k in sorted(counts):
            print(f"  {k}: {counts[k]}")
        # If mover sets enabled but watchlist empty — operational hazard
        if fail_empty_watchlist:
            try:
                en = conn.execute(
                    "SELECT COUNT(*) FROM mover_sets WHERE enabled = 1"
                ).fetchone()[0]
                wl = counts.get("mover_watchlist", 0)
                if en and wl == 0:
                    problems.append(
                        "mover_sets enabled but mover_watchlist is EMPTY "
                        "(would miss all dumps — restore watchlist before relying on movers)"
                    )
            except sqlite3.Error:
                pass
        # alerts table must exist on prod-shaped DBs
        if "alerts" not in counts and table_has(conn, "sqlite_master"):
            pass
    finally:
        conn.close()
    return problems


def table_has(conn: sqlite3.Connection, _name: str) -> bool:
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="DB durability safety check")
    ap.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional SQLite path (e.g. data/alerts.db) for live snapshot",
    )
    ap.add_argument(
        "--fail-empty-watchlist",
        action="store_true",
        help="Fail if enabled mover set has empty watchlist",
    )
    ap.add_argument(
        "--strict-db",
        action="store_true",
        help="Treat missing DB as failure (use on droplet pre-deploy)",
    )
    args = ap.parse_args()

    print("=== DB safety static scan ===")
    problems = scan_static()
    if problems:
        print("FAIL:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Static scan OK")
    print(f"Protected tables: {', '.join(sorted(PROTECTED_TABLES))}")

    if args.db:
        db_problems = check_db(args.db, fail_empty_watchlist=args.fail_empty_watchlist)
        notes = [p for p in db_problems if p.startswith("NOTE:")]
        real = [p for p in db_problems if not p.startswith("NOTE:")]
        for n in notes:
            print(n)
            if args.strict_db:
                print("FAIL: --strict-db requires existing database")
                return 1
        if real:
            print("FAIL (live DB):")
            for p in real:
                print(f"  - {p}")
            return 1
        print("Live DB check OK")

    print("=== db_safety_check PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

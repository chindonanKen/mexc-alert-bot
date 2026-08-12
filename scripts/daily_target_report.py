#!/usr/bin/env python3
"""Run the AD Desk daily target report (hits + near-misses).

Usage (repo root or container /app):
  python3 scripts/daily_target_report.py
  python3 -m mexc_bot.reports.daily_targets
  python3 scripts/daily_target_report.py --no-telegram --now

Env: ALERTS_FILE, DESK_USER_ID, TIMEZONE, DAILY_TARGET_REPORT_HOUR (default 6),
     DAILY_TARGET_NEAR_PCT (default 5), DAILY_TARGET_REPORT_DIR, TELEGRAM_BOT_TOKEN,
     DAILY_TARGET_REPORT_TELEGRAM=true|false
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily AD Desk target report")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument(
        "--now",
        action="store_true",
        help="Use current time as window end (debug); default snaps to last 6 AM cycle",
    )
    ap.add_argument("--user-id", type=int, default=None)
    ap.add_argument("--near-pct", type=float, default=None)
    args = ap.parse_args()

    # Load .env if present
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    from mexc_bot.reports.daily_targets import run_daily_target_report
    import time

    report = run_daily_target_report(
        user_id=args.user_id,
        near_pct=args.near_pct,
        send_telegram=False if args.no_telegram else None,
        now=time.time() if args.now else None,
    )
    print(report.to_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

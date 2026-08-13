#!/usr/bin/env bash
# Pre-deploy / pre-rebuild guard: never start a deploy that risks wiping SQLite.
#
# Usage (repo root or droplet ~/mexc-alert-bot):
#   bash scripts/pre_deploy_db_guard.sh
#   bash scripts/pre_deploy_db_guard.sh --strict   # require data/alerts.db
#
# Blocks:
#   - static code patterns (DROP live tables, rm data/, compose down -v)
#   - optional empty-watchlist when movers sets are enabled
#   - refuses if docker compose would run without ./data bind mount
#
# Does NOT delete or migrate data. Safe to run anytime.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STRICT=0
for a in "$@"; do
  case "$a" in
    --strict) STRICT=1 ;;
  esac
done

PY=python3
if [[ -x .venv/bin/python3 ]]; then PY=.venv/bin/python3; fi

echo "==> pre_deploy_db_guard: static + DB durability"
ARGS=()
if [[ -f data/alerts.db ]]; then
  ARGS+=(--db data/alerts.db --fail-empty-watchlist)
elif [[ "$STRICT" -eq 1 ]]; then
  echo "FAIL: --strict but data/alerts.db missing (refusing deploy that could create empty DB)"
  exit 1
else
  echo "NOTE: no data/alerts.db yet (local fresh clone OK)"
fi

$PY scripts/db_safety_check.py "${ARGS[@]+"${ARGS[@]}"}"

# Snapshot counts for post-deploy comparison (if DB present)
if [[ -f data/alerts.db ]]; then
  mkdir -p data/.safety
  $PY - <<'PY'
import json, sqlite3, time
from pathlib import Path
import sys
sys.path.insert(0, ".")
from mexc_bot.db_safety import snapshot_counts
p = Path("data/alerts.db")
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
snap = snapshot_counts(con)
ts = time.time()
wl_rows = []
try:
    wl_rows = [
        {"user_id": r[0], "symbol": r[1], "market": r[2], "set_id": r[3]}
        for r in con.execute(
            "SELECT user_id, symbol, market, set_id FROM mover_watchlist "
            "ORDER BY user_id, market, symbol"
        )
    ]
except Exception as e:
    print(f"NOTE: watchlist symbol snapshot skipped: {e}")
con.close()
out = Path("data/.safety/pre_deploy_snapshot.json")
out.write_text(json.dumps({"ts": ts, "counts": snap}, indent=2))
print(f"Wrote {out} ({len(snap)} tables)")
for k, v in sorted(snap.items()):
    if v:
        print(f"  {k}: {v}")
# Symbol list — counts alone cannot rebuild quiet coins after a wipe
if wl_rows or snap.get("mover_watchlist") == 0:
    wl = Path("data/.safety/watchlist_snapshot.json")
    wl.write_text(json.dumps({"ts": ts, "count": len(wl_rows), "rows": wl_rows}, indent=2))
    print(f"Wrote {wl} ({len(wl_rows)} watchlist rows)")
PY
  # Host/root writes must stay writable by the container appuser (uid 1000).
  # A root-owned .safety/schema.lock crash-looped Telegram (2026-08-13).
  if [[ -d data/.safety ]]; then
    chown -R 1000:1000 data/.safety 2>/dev/null || true
    chmod 775 data/.safety 2>/dev/null || true
  fi
fi

echo "==> pre_deploy_db_guard PASSED"

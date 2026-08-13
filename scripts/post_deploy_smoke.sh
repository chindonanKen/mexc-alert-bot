#!/usr/bin/env bash
# Post-deploy smoke: prove the bot is polling, not just that SQLite exists.
#
# Usage (repo root or droplet):
#   bash scripts/post_deploy_smoke.sh
#   bash scripts/post_deploy_smoke.sh --container mexc-alert-bot-staging --db data-staging/alerts.db
#   bash scripts/post_deploy_smoke.sh --wait 35
#
# Exit 1 = do not claim deploy done. Does not roll back automatically.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONTAINER="${SMOKE_CONTAINER:-mexc-alert-bot}"
DB="${SMOKE_DB:-data/alerts.db}"
WAIT_SEC=30
REQUIRE_HEARTBEAT=1
WATCHLIST_FLOOR=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --wait) WAIT_SEC="$2"; shift 2 ;;
    --no-heartbeat) REQUIRE_HEARTBEAT=0; shift ;;
    --skip-watchlist-floor) WATCHLIST_FLOOR=0; shift ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# //'
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

fail() { echo "SMOKE FAIL: $*"; exit 1; }
ok() { echo "  ok: $*"; }

echo "==> post_deploy_smoke container=$CONTAINER db=$DB wait=${WAIT_SEC}s"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not available"
fi

status() {
  docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null || echo "missing"
}

st="$(status)"
[[ "$st" == missing ]] && fail "$CONTAINER not found"
echo "  status now: $st"
if [[ "$st" == restarting* ]]; then
  fail "$CONTAINER is Restarting (crash loop) — $st"
fi
if [[ "$st" != running* ]] && [[ "$st" != healthy* ]] && [[ "$st" != *"starting"* ]] && [[ "$st" != running* ]]; then
  # inspect format is "running healthy" / "running starting" / "restarting"
  :
fi
echo "$st" | grep -q '^running' || fail "$CONTAINER is not running ($st)"

echo "  waiting ${WAIT_SEC}s for polling + first monitor cycle..."
sleep "$WAIT_SEC"

st="$(status)"
echo "  status after wait: $st"
echo "$st" | grep -q '^running' || fail "$CONTAINER died during wait ($st)"
echo "$st" | grep -q '^restarting' && fail "$CONTAINER crash-looping after wait"

logs="$(docker logs --tail 200 "$CONTAINER" 2>&1 || true)"
echo "$logs" | grep -q "Starting Telegram bot polling" || fail "log missing 'Starting Telegram bot polling'"
ok "Telegram polling started"

if echo "$logs" | grep -q "PermissionError"; then
  # Recent crash-loop signature — fail even if it later recovered
  if echo "$logs" | tail -40 | grep -q "PermissionError"; then
    fail "PermissionError in recent logs (last night's crash class)"
  fi
fi

if echo "$logs" | grep -q "Monitor cycle:"; then
  ok "monitor cycle ran"
else
  echo "  note: no Monitor cycle line yet (0 alerts is still ok if polling is up)"
fi

DATA_DIR="$(dirname "$DB")"
HB="$DATA_DIR/bot_heartbeat.json"
if [[ "$REQUIRE_HEARTBEAT" -eq 1 ]]; then
  if [[ ! -f "$HB" ]]; then
    # Host path vs container path: heartbeat is in the bind-mount
    fail "heartbeat missing at $HB — bot is not writing live proof"
  fi
  PY=python3
  if [[ -x .venv/bin/python3 ]]; then PY=.venv/bin/python3; fi
  "$PY" - "$HB" <<'PY' || fail "heartbeat stale or polling=false"
import json, sys, time
p = sys.argv[1]
d = json.loads(open(p).read())
age = time.time() - float(d.get("ts") or 0)
if age > 90:
    print(f"heartbeat age {age:.0f}s")
    sys.exit(1)
if not d.get("polling"):
    print("polling flag not set")
    sys.exit(1)
print(f"heartbeat age {age:.1f}s polling={d.get('polling')} monitor={d.get('monitor')}")
PY
  ok "heartbeat fresh + polling"
fi

if [[ -f "$DB" ]]; then
  PY=python3
  if [[ -x .venv/bin/python3 ]]; then PY=.venv/bin/python3; fi
  SNAP="$DATA_DIR/.safety/pre_deploy_snapshot.json"
  WL_SNAP="$DATA_DIR/.safety/watchlist_snapshot.json"
  "$PY" - "$DB" "$SNAP" "$WL_SNAP" "$WATCHLIST_FLOOR" <<'PY' || fail "row-count gate"
import json, sqlite3, sys
db, snap_p, wl_p, floor = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
alerts = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
try:
    wl = c.execute("SELECT COUNT(*) FROM mover_watchlist").fetchone()[0]
except sqlite3.Error:
    wl = 0
print(f"live alerts={alerts} watchlist={wl}")
if snap_p and __import__("pathlib").Path(snap_p).is_file():
    snap = json.loads(open(snap_p).read())
    counts = snap.get("counts") or {}
    prev_a = int(counts.get("alerts") or 0)
    if prev_a > 0 and alerts < prev_a:
        print(f"alerts shrank {prev_a} -> {alerts}")
        sys.exit(1)
    prev_w = int(counts.get("mover_watchlist") or 0)
    if floor and prev_w > 0 and wl < prev_w:
        print(f"watchlist shrank {prev_w} -> {wl}")
        sys.exit(1)
if floor and __import__("pathlib").Path(wl_p).is_file():
    wls = json.loads(open(wl_p).read())
    n = int(wls.get("count") or 0)
    if n > 0 and wl < n:
        print(f"watchlist below snapshot {n} -> {wl}")
        sys.exit(1)
c.close()
PY
  ok "alerts/watchlist counts held"
else
  echo "  note: no $DB (fresh staging ok)"
fi

echo "==> post_deploy_smoke PASSED ($CONTAINER)"

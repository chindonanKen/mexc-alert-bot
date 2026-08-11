# Database durability — hard safety rule

**Owner rule (2026-08-11):** Nothing in the SQLite database is ever erased or wiped by **deploy**, **Docker rebuild**, **AD Desk update**, or **schema migration**.

Production money/sensor memory lives in one file (typically `./data/alerts.db` on the droplet). Losing it loses targets, mover watchlists, lessons, and learning events.

---

## The rule (non‑negotiable)

| Allowed | Forbidden |
|---------|-----------|
| `CREATE TABLE IF NOT EXISTS` | `DROP TABLE` on live data tables (except verified rebuild) |
| `ALTER TABLE … ADD COLUMN` | Migrations that `DELETE FROM` whole tables |
| Verified table rebuild via `safe_rebuild_table` (copy all rows, then swap) | Swap/rename that leaves an **empty** table when source had rows |
| User-initiated deletes (one alert, one lesson, `/mw remove`) | `docker compose down -v` on prod, `rm -rf data/`, volume prune of `./data` |
| Additive indexes | “Reset DB on startup”, seed scripts against prod |

**Deploy and rebuild only replace code containers.** Host bind mount `./data:/app/data` keeps the DB on disk.

---

## How schema changes must be done

Use helpers in **`mexc_bot/db_safety.py`**:

```python
from mexc_bot.db_safety import ensure_column, create_table_if_not_exists, safe_rebuild_table

# New table
create_table_if_not_exists(conn, """
CREATE TABLE IF NOT EXISTS my_feature (
  id INTEGER PRIMARY KEY,
  ...
)
""")

# New column (preferred — never rewrites the table)
ensure_column(conn, "learning_lessons", "new_field", "TEXT")

# PK / shape change only when SQLite cannot ALTER in place:
safe_rebuild_table(
    conn,
    table="my_table",
    create_new_ddl="CREATE TABLE my_table_new (...)",
    copy_sql="INSERT INTO my_table_new (...) SELECT ... FROM my_table",
)
# Raises SchemaSafetyError if after_count < before_count — live table stays.
```

**Do not** hand-roll `DROP TABLE mover_watchlist` / empty recreate. That class of bug emptied the watchlist and missed dumps (BLUAI).

---

## Enforcement (automatic)

| Gate | What it does |
|------|----------------|
| `python3 scripts/db_safety_check.py` | Static scan: bans live `DROP TABLE`, `rm data/`, `compose down -v`; optional live DB snapshot |
| `bash scripts/pre_deploy_db_guard.sh [--strict]` | Pre-deploy: static + require `data/alerts.db` on prod + snapshot counts + empty-watchlist fail |
| `make db-safety` / `make pre-deploy` | Local shortcuts |
| `scripts/verify_build.sh` | Runs static DB safety check |
| `scripts/deploy.sh` | Runs guard **before and after** `git pull`, then compose up (**never** `-v`) |
| `scripts/droplet.sh deploy-prod` | Same guard on the droplet |
| `tests/test_db_safety.py` | Unit tests: rebuild abort, migration keep rows, static scan green |

Agents and humans: **do not ship desk/bot changes without `make test` including `test_db_safety`**. On droplet deploy, if the guard fails (e.g. empty watchlist with movers ON), **fix data first** (`POST /api/watchlist/restore-from-fires`) — do not “fix” by wiping the DB.

---

## What is still allowed to delete rows

Application features, **not** migrations:

- Remove / clear **user-selected** target alerts
- Remove watchlist symbols, delete one lesson, answer/clear pending
- Local only: `scripts/seed_desk_local.py --force` against a **local** DB (never prod `./data` on the droplet)

These are intentional product actions. They must never run from `_init_db` / `_migrate*`.

---

## Protected tables (monitored)

See `PROTECTED_TABLES` in `mexc_bot/db_safety.py` — includes `alerts`, mover tables, learning/*, journal, cases, news, etc.

Pre-deploy writes `data/.safety/pre_deploy_snapshot.json` (row counts) for forensics.

---

## Recovery if something was already wiped

1. **Do not** re-init an empty DB over a backup.
2. Restore from host backup of `./data` if you have one.
3. Movers: desk **Restore from recent fires** or `POST /api/watchlist/restore-from-fires?days=7`.
4. Targets: only recoverable from backup / re-entry — treat alerts as sacred.

---

## Related

- [AGENTS.md](../Agents.md) — engineering constitution (this rule is mirrored there)
- [DROPLET_OPS.md](DROPLET_OPS.md) — deploy paths
- Mover wipe incident: empty `mover_watchlist` after unsafe PK migration

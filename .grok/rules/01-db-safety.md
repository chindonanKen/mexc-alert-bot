# Database durability (mandatory)

**Owner rule (2026-08-11):** Nothing in SQLite is erased by deploy, rebuild, AD Desk update, or migration.

## When writing schema / storage code

1. Use `mexc_bot/db_safety.py`: `ensure_column`, `create_table_if_not_exists`, `safe_rebuild_table`.
2. Prefer **ADD COLUMN** over table rebuild.
3. Never `DROP TABLE` a live table except inside `safe_rebuild_table` (verified row copy first).
4. Never put bulk `DELETE FROM` in `_init_db` / `_migrate*`.
5. Never add `docker compose down -v` or `rm` of `data/` to deploy scripts.

## Before claim done / deploy

```bash
python3 scripts/db_safety_check.py
# on droplet / with data:
bash scripts/pre_deploy_db_guard.sh --strict
```

Full doc: [docs/DB_SAFETY.md](../../docs/DB_SAFETY.md).

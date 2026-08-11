# Telegram data safety — lists must not wipe by accident

**Owner rule (2026-08-12):** A typo, bad coin name, or wrong subcommand must **never** erase movers watchlist, target alerts, or other durable state.

## Movers `/mw` (the list that got wiped)

| Command | Effect | Wipe risk |
|---------|--------|-----------|
| `/mw` / `/mw list` | Show list | None |
| `/mw add f BTC` / `/mw add s SIREN` | **Append** only | None — failed resolve leaves list intact |
| `/mw BTC` or `/mw f COIN` (bare symbols) | **Append** (same as add) | None — **not** replace |
| `/mw remove SIREN` | Remove matching row(s) | Only named coins |
| `/mw clear` | Refused | Must use `/mw clear confirm` |
| `/mw clear confirm` | Full clear | Explicit only |
| `/mw replace f A B` | Full replace of **Default** set | Explicit only; **aborts** if any symbol fails resolve |

### Storage hard stop

`MoverStore.set_watchlist(user, [])` **raises** if the set already has coins, unless `force_empty=True`.

### Regression gates

- `tests/test_mw_data_safety.py`
- `scripts/db_safety_check.py` (static strings for confirm / abort / force_empty)
- `make test` includes the above

## Target alerts

| Command | Effect | Guard |
|---------|--------|--------|
| `/a` `/af` | Add | No wipe |
| `/r` | Remove by id/symbol | Targeted only |
| `/clearall` | Wipe targets | Requires **`confirm`** |
| `/disableall` | Disable, keeps rows | Non-destructive |

## What not to reintroduce

- Bare `/mw SYMBOL…` → `set_watchlist` (full replace) — **forbidden**
- `set_watchlist` with empty `items` after failed resolve — **forbidden**
- `/mw clear` without confirm — **forbidden**
- Migrations that `DROP` live `mover_watchlist` without `safe_rebuild_table` — see [DB_SAFETY.md](DB_SAFETY.md)

## Restore after accidental wipe

Desk: **Restore from recent fires** or:

```bash
curl -X POST "…/api/watchlist/restore-from-fires?days=7" -H "X-Desk-Token: …"
```

Quiet coins that never fired in the window must be re-added with `/mw add`.

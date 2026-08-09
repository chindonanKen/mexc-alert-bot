# Mandatory AD Desk QA (`desk-qa`)

**Owner rule (2026-08-09):** every AD Desk iteration must run the multi-agent QA panel before you claim done.

## When it applies

Any change under:

- `mexc_bot/webapi/` (API + desk SPA)
- `mexc_bot/learning/`
- `mexc_bot/webapi/static/` (`desk.js` / `desk.css` / `index.html`)
- `.grok/workflows/desk-qa.rhai`

Docs-only / pure Telegram bot paths without desk touch: **no** gate.

## Required sequence (every desk iteration)

1. Implement + local smoke (tests if relevant).
2. **Run workflow `desk-qa`** with `args.focus` = one-line description of the change  
   (`workflow` tool `name: desk-qa` or `/workflow desk-qa`).
3. If verdict is **FAIL** or has **blockers**: fix them, re-run `desk-qa`.
4. **Mark pass:**  
   `python3 scripts/desk_qa_gate.py pass --note "desk-qa PASS: <one line>"`
5. Only then ship/deploy narrative and short report (verdict + blockers).

## Enforcement

- Grok **Stop hook** blocks ending the turn while desk edits are dirty and unpassed (see `.grok/hooks/desk-qa-mandatory.json`).
- Project hooks need **folder trust** (`/hooks-trust` once per machine).
- Do **not** `pass` without actually running the panel — owner is burning tokens on rework.

## Agents inside `desk-qa`

1. New functions wired end-to-end  
2. UI/UX quality  
3. Regressions (alerts, money_truth, teach_ok, hold book, existing endpoints)

## Status helpers

```bash
python3 scripts/desk_qa_gate.py status
make desk-qa   # prints how to run + status
```

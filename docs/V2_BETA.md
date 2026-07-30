# V2 Beta — AD Desk (agent trading platform)

**Status:** Beta shippable  
**Commit series:** V2 desk UI + API  
**Telegram:** still primary for live panic push  
**Desk:** overview + memory + intel + coach for decision support  

---

## What this beta is

A **futuristic command desk** for Kenneth’s AD / panic strategy:

| Surface | Purpose |
|---------|---------|
| **Overview** | Regime (BTC/ETH/SOL), counters, recent fires, isolated checks |
| **Tape & Heat** | Mover watchlist + live marks |
| **Targets** | One-shot alert list |
| **Memory** | Events + one-click label latest took/skip |
| **Intel** | Fatal news, CEX delist cache, source expertise weights |
| **Agent** | Rule-based coach + brief (LLM fluent coach later) |
| **Playbook** | Encoded AD prefer/avoid from strategy |

**Planned in UI, not wired yet:** voice notes, MEXC fill journal (V2.1 when UX is ready).

---

## Design principles (your sessions)

1. **Glance regime first** — risk-off / range / risk-on from majors  
2. **Panic vs isolated** — heat + isolated agent verdicts  
3. **News as veto** — delist/hack intel, not FOMO feed  
4. **Memory without slash soup** — labels from desk buttons  
5. **Telegram keeps the siren** — desk does not replace push  

---

## Run locally

```bash
cd ~/mexc-bot
pip install -r requirements.txt
export ALERTS_FILE=data/alerts.json   # or path to prod/staging db copy
export DESK_API_TOKEN=dev-token       # optional; empty = open local
python -m mexc_bot.webapi
# open http://127.0.0.1:8080
# if token set: http://127.0.0.1:8080/?token=dev-token
```

---

## Run on droplet (alongside bot)

```bash
cd ~/mexc-alert-bot
git pull origin main

# in .env:
# DESK_API_TOKEN=<long random>
# DESK_USER_ID=<your telegram id>   # if multi-user db
# DESK_PORT=8080

docker compose --profile desk up -d --build mexc-desk
docker logs --tail 40 mexc-ad-desk
```

Open: `http://YOUR_DROPLET_IP:8080/?token=YOUR_DESK_API_TOKEN`  
Firewall: allow **8080** only from your IP if possible.

Desk mounts **prod `./data` read-only** — does not write alerts via UI except labels into learning tables (label POST).

---

## API (for agents / future apps)

| Method | Path | Role |
|--------|------|------|
| GET | `/api/health` | liveness |
| GET | `/api/overview` | regime + counters + recent |
| GET | `/api/alerts` | targets |
| GET | `/api/watchlist` | mw + tickers |
| GET | `/api/events` | learning fires |
| POST | `/api/events/label` | took/skip |
| GET | `/api/investigations` | isolated agent + sources |
| GET | `/api/news` | news + delist cache |
| GET | `/api/prices` | majors |
| POST | `/api/coach` | brief / coach text |
| GET | `/api/strategy` | playbook JSON |

Auth: header `X-Desk-Token: …` or `Authorization: Bearer …` when `DESK_API_TOKEN` set.

---

## Recommended next (after beta soak)

1. Fluent LLM coach with tools (read-only)  
2. Voice + MEXC fills **in desk UX** (not Telegram slash)  
3. Live WS price stream  
4. Mobile PWA install + dark widgets  
5. Optional write actions (mw add) behind confirm  

---

## Safety

- Does not run Telegram polling  
- Does not place orders  
- Label writes only to `learning_labels`  
- Prod bot process stays independent  

<!-- agents: search V2_BETA AD Desk -->

# Staging stress tests

## Can the agent use Telegram as you?

**No.** Grok cannot log into your Telegram account or click buttons in the app.

| Possible | Not possible |
|----------|----------------|
| Automated stress of DB, scanners, news, fills, keyboards | Acting as *you* in a private chat |
| Bot **sending** test messages *to* you (if token + chat_id) | Receiving `/start` as a user without a user client |
| You (or droplet Grok) run scripts on the server | Bypassing Telegram without the Bot API |

The **staging container** already owns `getUpdates` polling. A second poller with the same token causes **409 Conflict** and silence.

---

## Automated stress (recommended)

On the **droplet** (or Mac with venv):

```bash
cd ~/mexc-alert-bot   # or your path
git pull origin main
./scripts/stress_staging.sh

# harder:
STRESS_EVENTS=2000 STRESS_THREADS=16 ./scripts/stress_staging.sh
```

What it hammers:

- Hundreds/thousands of learning events + labels  
- Concurrent SQLite writes  
- Outcome pending/record load  
- 200 price-history series  
- News classify + fingerprint dedupe  
- Fill insert dedupe storm  
- Keyboard/callback generation at scale  

---

## Optional: bot → you message flood

Does **not** steal polling (only `sendMessage`):

```bash
# Your numeric chat id: message @userinfobot or check getUpdates once offline
export STAGING_BOT_TOKEN='...'      # staging only
export STAGING_CHAT_ID='123456789'
STRESS_NOTIFY=1 STRESS_NOTIFY_N=5 ./scripts/stress_staging.sh
```

You should see 5 “STRESS TEST” messages from the staging bot.

---

## Human Telegram limit push (you)

On **@MEXC_Alerts_Stagingbot**, as fast as you can:

1. `/desk` `/s` `/brief` `/events` `/news` `/coach panic`  
2. Type `took` `skip` `later` `brief` ten times  
3. `/p BTC` × 20  
4. `/mw add f BTC ETH SOL` then `/movers on`  
5. On next fire: tap **Took** then bounce buttons rapidly  
6. Watch logs: `docker logs -f mexc-alert-bot-staging` — no crash loop  

**Pass:** bot still replies; prod bot still fine; no 409; container Up.

---

## What “limits” mean here

| Limit | How we push it |
|-------|----------------|
| SQLite under learning load | bulk + concurrent tests |
| Notify with keyboards | keyboard gen + optional sendMessage |
| News false flags | classify noise vs fatal corpus |
| Fill sync uniqueness | 300 inserts → 50 unique |
| Live handler latency | you spam commands |

<!-- agents: search STAGING_STRESS -->

# V2.1 Beta — AD Command Desk

**Status:** Beta 2.1 (CRUD + positions + Grok voice tools + roadmap)  
**Telegram:** panic push stays primary  
**Desk:** control surface for overview, book, intel, agent  

---

## Vision (finished V2)

A **futuristic agent trading platform** that co-pilots AD panic scale-ins:

1. Sensors fire instantly (Telegram)  
2. Desk shows regime, heat, positions, memory, intel  
3. **Grok voice co-pilot** manipulates the terminal (alerts, watch, journal, labels, trade *plans*)  
4. Specialist agents (isolated dump / delist) learn source reliability  
5. Next: realtime speech-to-speech, MEXC fills, layer planner, optional gated live orders, PWA  

**Principle:** Journal and plan first. Live exchange orders only if explicitly enabled later (`DESK_ALLOW_LIVE_ORDERS`, placement not shipped yet).

---

## What's in this beta

| Area | Capability |
|------|------------|
| **UI** | Modern dark glass · Instrument Sans · IBM Plex Mono · mobile rail |
| **Overview** | Regime, metrics, open positions, fires, isolated checks |
| **Positions** | Open / close AD journal trades |
| **Tape** | Watchlist add/remove · mover on/off · threshold |
| **Targets** | Add / enable / disable / delete alerts |
| **Memory** | Events + Took/Skip |
| **Intel** | News, delist radar, source expertise |
| **Voice Agent** | Hold-to-talk → xAI STT → tool loop → optional TTS |
| **Roadmap** | Live product map now vs next |
| **Playbook** | Encoded AD prefer/avoid |

---

## Grok / xAI voice

```bash
XAI_API_KEY=xai-...
XAI_API_BASE=https://api.x.ai/v1
XAI_CHAT_MODEL=grok-3
DESK_VOICE_TTS=true
DESK_USER_ID=<telegram id>
DESK_API_TOKEN=<long random>
```

Tools: add/delete/list alerts, watchlist, movers settings, positions, label_fire, overview, propose_trade.

Realtime `wss://api.x.ai/v1/realtime?model=grok-voice-latest` is the next voice upgrade.

---

## Deploy

```bash
cd ~/mexc-alert-bot && git pull
# set DESK_* and XAI_API_KEY in .env
docker compose --profile desk up -d --build mexc-desk
```

Open `http://IP:8080/?token=…`  
Desk volume is **read-write** for CRUD (same `./data` as bot).

---

## Safety

- Does not replace Telegram polling  
- Does not place MEXC orders by default  
- Destructive deletes require UI confirm; voice confirms in reply  
- Prefer journal `open_position` / `propose_trade` over live risk  

<!-- agents: search V2_BETA -->

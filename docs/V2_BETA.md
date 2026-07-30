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

### Microphone (required: HTTPS) — no file upload

Browsers **block the mic on `http://IP:…`**. There is **no sound-file upload** path.  
Use **HTTPS** so you can fully explore the desk including voice.

**Voice pipeline:** mic (WebM/mp4/ogg) → desk **ffmpeg** → 16 kHz mono **WAV** → xAI STT → grok tools → reply.  
Raw WebM is rejected by xAI STT (`Unsupported or corrupt audio format: webm`); conversion is mandatory.  
Text agent (`/api/agent`) does not need ffmpeg and works even if STT is broken.

```bash
# On droplet (full desk including mic)
./scripts/desk_https_up.sh
# Open: https://DROPLET_IP/?token=DESK_API_TOKEN
# Accept self-signed cert once → allow mic → Voice tab
```

Stack: `mexc-desk` (app, internal) + `mexc-desk-https` (Caddy TLS on **443** / redirect **80**).

| URL | Mic | Notes |
|-----|-----|--------|
| `https://IP/?token=…` | Yes | Correct full-desk URL |
| `http://IP:8080/…` | No | Not published by default; do not use for voice |
| `http://localhost:8080` | Yes* | Local `make desk` only (localhost is a secure context) |

---

## Deploy (full exploration)

```bash
cd ~/mexc-alert-bot && git pull origin main
# .env must include DESK_API_TOKEN, DESK_USER_ID, XAI_API_KEY
./scripts/desk_https_up.sh
```

Open **`https://DROPLET_IP/?token=…`** (not `:8080`).  
Desk volume is **read-write** for CRUD (same `./data` as bot).  
Text agent + quick chips always work over HTTPS; mic needs the secure URL + browser permission.

---

## Safety

- Does not replace Telegram polling  
- Does not place MEXC orders by default  
- Destructive deletes require UI confirm; voice confirms in reply  
- Prefer journal `open_position` / `propose_trade` over live risk  

<!-- agents: search V2_BETA -->

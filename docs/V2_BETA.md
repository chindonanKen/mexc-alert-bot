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

### Two ways to open (same token)

| URL | Mic | When to use |
|-----|-----|-------------|
| `http://IP:8080/?token=…` | No | Droplet HTTP guide — overview, CRUD, **text** agent |
| `https://IP/?token=…` | Yes | Voice / mic (self-signed cert → Advanced → Proceed) |
| `http://localhost:8080` | Yes* | Local `make desk` only |

```bash
# HTTP only (matches droplet Grok guide — no mic)
./scripts/desk_up.sh
# Open: http://DROPLET_IP:8080/?token=…

# Full stack: HTTP :8080 + HTTPS :443 for mic
./scripts/desk_https_up.sh
# Mic:  https://DROPLET_IP/?token=…
# Text: http://DROPLET_IP:8080/?token=…
```

HTTPS uses **openssl self-signed cert with IP SAN** (not Caddy `tls internal`, which is flaky on bare IPs).  
No HSTS — you can always fall back to `:8080` if TLS misbehaves.

Token (on droplet, do not paste into shared chats):

```bash
grep '^DESK_API_TOKEN=' ~/mexc-alert-bot/.env
```

---

## Local dev (fast edits — Mac + Xplor)

Work on this machine before pushing to the droplet:

```bash
cd ~/mexc-bot
make desk-dev          # or: ./scripts/desk_dev.sh
```

| Item | Detail |
|------|--------|
| URL | `http://127.0.0.1:8080/?token=…` (printed by the script) |
| Browser | Opens **Xplor** when `/Applications/Xplor.app` exists |
| Mic | Works on **localhost** (secure context — no Caddy) |
| Reload | `DESK_RELOAD=true` — Python reloads on save |
| Static UI | Edit `desk.js` / `desk.css` → **Cmd+Shift+R** in Xplor |
| Secrets | First run creates `.env` — add `XAI_API_KEY` for voice |
| Data | Local `./data` only — not prod droplet DB |

When ready for prod:

```bash
git push origin main
# droplet: git pull && ./scripts/desk_https_up.sh
```

---

## Deploy (full exploration)

```bash
cd ~/mexc-alert-bot && git pull origin main
# .env: DESK_API_TOKEN, DESK_USER_ID, XAI_API_KEY
./scripts/desk_https_up.sh   # preferred
# or HTTP-only: ./scripts/desk_up.sh
```

Desk volume is **read-write** for CRUD (same `./data` as bot). Does not poll Telegram or place orders.

---

## Safety

- Does not replace Telegram polling  
- Does not place MEXC orders by default  
- Destructive deletes require UI confirm; voice confirms in reply  
- Prefer journal `open_position` / `propose_trade` over live risk  

<!-- agents: search V2_BETA -->

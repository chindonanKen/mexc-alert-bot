# Droplet ops — agent + Kenneth with fewer handoffs

**Goal:** Code and verification on the **DigitalOcean droplet** where Docker + MEXC + prod/staging volumes already live. Laptop Grok is optional for offline edits; **runtime truth is the droplet**.

Related: [STAGING.md](STAGING.md) · [VERIFY_BUILD.md](VERIFY_BUILD.md) · [AGENTS.md](../AGENTS.md)

---

## Recommended model (least loops)

```text
┌─────────────────────┐     git push      ┌──────────────────┐
│ Mac Grok (optional) │ ───────────────►  │ GitHub main      │
└─────────────────────┘                   └────────┬─────────┘
                                                   │ git pull
         ┌─────────────────────────────────────────▼──────────┐
         │  DigitalOcean droplet                               │
         │  ~/mexc-alert-bot                                   │
         │  ┌─────────────────────────────────────────────┐  │
         │  │ Grok Build / Grok CLI *on the droplet*       │  │
         │  │  → docker compose, logs, .env.staging, tests │  │
         │  └─────────────────────────────────────────────┘  │
         │  mexc-alert-bot        (prod  ./data)              │
         │  mexc-alert-bot-staging (staging ./data-staging)  │
         └────────────────────────────────────────────────────┘
```

**Why Grok on the droplet is better for this project**

| On laptop only | On droplet |
|----------------|------------|
| Can’t reach MEXC (some ISPs block) | MEXC + Telegram work |
| No Docker (this MacBook) | Docker is already how you run prod |
| You paste logs / run SSH | Agent runs `docker logs`, restarts, staging |
| Deploy = you in the middle | Agent deploys after you approve once |

**What you still approve (human gate)**

1. Merging / pushing to `main` (or saying “deploy this”)  
2. Enabling prod flags (`FEATURE_LEARNING` on **prod** `.env`)  
3. Any destroy/recreate of prod data  
4. Putting secrets into `.env` / `.env.staging` the first time  

Everything else can be agent-driven on the droplet: pull, rebuild staging, read logs, run `verify_build.sh`, adjust staging env (not prod tokens in chat).

---

## Option A — Grok Build / CLI **on the droplet** (preferred)

One-time on the droplet (you or agent via SSH):

```bash
# 1) Repo
cd ~
# if needed: git clone https://github.com/chindonanKen/mexc-alert-bot.git mexc-alert-bot
cd ~/mexc-alert-bot
git pull origin main

# 2) Install Grok CLI / open Grok Build workspace on this folder
#    (use whatever install path xAI documents for Linux)
# 3) Open session with cwd = ~/mexc-alert-bot
```

**Bootstrap prompt for every droplet Grok session:**

```text
You are on the DigitalOcean droplet for mexc-alert-bot (~/mexc-alert-bot).

READ: AGENTS.md, docs/DROPLET_OPS.md, docs/STAGING.md, docs/VERIFY_BUILD.md, docs/SESSION_HANDOFF.md

RULES:
- NEVER touch prod ./data or prod .env tokens unless I explicitly say "prod".
- Default work target = STAGING: profile staging, data-staging, .env.staging.
- Prod container mexc-alert-bot must keep running while we test.
- Run ./scripts/verify_build.sh after code changes.
- After staging changes: docker compose --profile staging up -d --build mexc-bot-staging
- Show docker ps + last 40 lines of staging logs; ask me only for approvals (prod flag flips, deploys to prod, secrets).

Current task: <describe>
```

With that, the agent can:

- `git pull` / edit / test  
- Rebuild **staging only**  
- Read `docker logs mexc-alert-bot-staging`  
- Confirm prod still up: `docker ps | grep mexc-alert-bot`  
- Update handoff docs  

You only reply when it asks: “promote learning to prod?” / “push?” / “restart prod?”

---

## Option B — Laptop Grok + SSH to droplet (fallback)

When Grok runs on the Mac, give the agent **passwordless SSH** to one host alias.

### 1. On Mac — `~/.ssh/config` (example)

```sshconfig
Host mexc-droplet
  HostName YOUR_DROPLET_IP
  User root
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

Test: `ssh mexc-droplet 'hostname && docker ps --format "{{.Names}}"'`

### 2. Agent uses remote helpers

```bash
./scripts/droplet.sh status
./scripts/droplet.sh staging-up      # needs .env.staging already on server
./scripts/droplet.sh staging-logs
./scripts/droplet.sh staging-down
./scripts/droplet.sh prod-logs
./scripts/droplet.sh deploy-staging  # git pull + rebuild staging only
./scripts/droplet.sh deploy-prod     # git pull + rebuild prod — CONFIRM first
```

`DROPLET_SSH_HOST` defaults to `mexc-droplet` (override if needed).

**Approval policy for agents (Option B):**

| Action | Needs Kenneth? |
|--------|----------------|
| `status`, `staging-logs`, `prod-logs` | No |
| `deploy-staging`, `staging-up/down` | No (staging only) |
| `deploy-prod`, edit prod `.env` | **Yes** — ask first |
| Delete `data/` or force-recreate prod | **Yes** |

---

## Option C — GitHub → droplet (semi-auto)

1. Laptop/agent pushes `main`  
2. Droplet cron or webhook: `git pull && docker compose --profile staging up -d --build mexc-bot-staging`  
3. Prod rebuild only on tagged release or manual `deploy-prod`  

Useful later; not required if Option A works.

---

## Staging on the droplet (next step for V4 learning)

```bash
ssh mexc-droplet   # or Grok session already on server
cd ~/mexc-alert-bot
git pull origin main

cp -n .env.staging.example .env.staging
# set TELEGRAM_BOT_TOKEN to staging bot (BotFather) — only on server, never commit

mkdir -p data-staging
chown -R 1000:1000 data-staging data 2>/dev/null || true

# Prod keeps running
docker compose up -d mexc-bot

# Staging alongside
docker compose --profile staging up -d --build mexc-bot-staging
docker ps
docker logs --tail 80 mexc-alert-bot-staging
```

Expect logs: `learning=True`, polling started.  
Telegram: **staging bot only** → `/s` `/events` `/brief`.

Prod: original bot + `./data` unchanged.

---

## Security

- Never put prod or staging tokens in GitHub or chat if avoidable  
- Staging token that was pasted in a laptop chat: **rotate in BotFather** if the chat isn’t private  
- `.env` / `.env.staging` stay on the droplet only  
- Agents: no `docker compose down` (drops both); stop **named** services only  

---

## Decision matrix (pick one)

| Preference | Do this |
|------------|---------|
| **Least handoffs** | Install Grok on droplet; work only there for ops (Option A) |
| Keep Grok on Mac | Set `Host mexc-droplet` SSH + use `./scripts/droplet.sh` (Option B) |
| Hands-off deploys | Later: webhook/cron pull (Option C) |

<!-- agents: search DROPLET_OPS -->

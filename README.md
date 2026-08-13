# Doctor Appointment Setter Agent (Bangla, multi-tenant SaaS)

A multi-tenant, Bangla-first appointment-setting platform for hospitals/clinics
in Bangladesh. A tool-calling LangGraph agent talks to patients over **chat,
WhatsApp, SMS, browser voice, and SIP telephony** — finds a doctor, checks
real availability, collects patient details, and books — the same agent core
behind every channel. A **FastAPI backend** exposes the agent plus the full
REST API; a **Next.js app** is both a hospital staff/admin console and a
patient-facing doctor-marketplace **portal** (search, book, pay, review). The
platform is monetized (per-booking fees + patient/hospital subscriptions) and
runs **fully locally by default** — local LLM (Ollama), local STT/TTS, no
required cloud dependency — with optional cloud fallbacks (OpenRouter or Gemini
LLM, Gemini STT/TTS, LiveKit Inference) you can turn on per-component.

> Architecture deep-dive: **[ARCHITECTURE.md](./ARCHITECTURE.md)**.
> Monetization & billing: **[docs/MONETIZATION.md](./docs/MONETIZATION.md)**.
> Feature-specific setup: **[WhatsApp/SMS](./docs/WHATSAPP_AND_SMS.md)** ·
> **[Telephony](./docs/TELEPHONY.md)** · **[Browser voice](./docs/VOICE_WEB.md)** ·
> **[Scaling](./docs/SCALING.md)**.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Quick start](#quick-start)
4. [Docker & deployment](#docker--deployment)
5. [Setup in detail](#setup-in-detail)
6. [Running the FastAPI backend](#running-the-fastapi-backend)
7. [Talking to the agent](#talking-to-the-agent)
8. [REST API](#rest-api)
9. [Admin UI](#admin-ui)
10. [Monetization & billing](#monetization--billing)
11. [Environment variables](#environment-variables)
12. [Command reference](#command-reference)
13. [Conversation flow](#conversation-flow)
14. [Voice (local mic)](#voice-local-mic)
15. [Troubleshooting](#troubleshooting)

---

## Architecture

```
Patient (chat / WhatsApp / SMS / browser voice / SIP)
        │
        ▼
FastAPI backend  ──  /chat, /chat/ws, full REST (see docs/), gateway webhooks
        │
        ▼
LangGraph tool-calling agent  ──ChatOllama──▶  Ollama (gemma4:latest, local)
        │  (books, searches, checks RAG docs, escalates — LLM picks the tool)
        ▼
PostgreSQL  ◀── multi-tenant schema + LangGraph checkpoints/store + RAG vectors ──▶ Next.js app
                                                                    (staff console + patient portal,
                                                                     fully API-driven, no direct DB)
```

- The **LLM decides which tool to call** (book, search doctors, check docs,
  escalate to a human); Python only decides when a turn ends. See
  [ARCHITECTURE.md §2](./ARCHITECTURE.md#2-the-backend) for the full design,
  including the KV-cache-aware tool-binding strategy that keeps a small local
  CPU model fast.
- Each turn is **one graph invocation**; the Postgres checkpointer restores
  conversation state between turns (keyed by `session_id`).
- Availability is **computed** from `doctor_schedule` minus confirmed/held
  `appointments` — no calendar service.
- Everything patient-facing is **LLM-composed**, except payment links and
  upgrade prompts, which are deterministic UI chrome that never passes
  through the LLM (can't be hallucinated).

---

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.10–3.13 | backend + agent |
| PostgreSQL | 13+ | appointment data + checkpoints |
| Ollama | latest | the local LLM |
| Node.js | 18.18+ | the admin UI |

Install system tools (Debian/Ubuntu example):

```bash
sudo apt update && sudo apt install -y python3-venv postgresql
# Node via nvm/nodesource; Ollama from https://ollama.com/download
```

---

## Quick start

> Prefer containers? Skip this section — `docker compose up --build -d` brings
> up the whole stack. See [Docker & deployment](#docker--deployment).

```bash
# 1. Services
sudo service postgresql start
ollama serve &                 # if not already running
ollama pull gemma4:latest

# 2. Database
createdb appointments

# 3. Python backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit DATABASE_URL to match your Postgres
alembic upgrade head            # schema is managed by Alembic migrations

# 4. Run
uvicorn api.app:app --reload --port 8000
# open http://localhost:8000/docs
```

Then in another terminal, start a conversation (see
[Talking to the agent](#talking-to-the-agent)).

---

## Docker & deployment

The whole application is dockerized — Postgres (pgvector), Ollama, the FastAPI
backend, the Next.js UI, and an optional voice-worker profile. The backend
container applies Alembic migrations automatically on start, so a fresh
checkout becomes a running system in three commands:

```bash
cp .env.example .env
# edit .env — at minimum set strong values for:
#   POSTGRES_PASSWORD=...          (compose refuses to start without it)
#   JWT_SECRET=...                 (e.g. openssl rand -hex 32)

docker compose up --build -d       # postgres + ollama + backend + ui
docker compose logs -f backend     # watch migrations + LLM warmup
```

First start downloads the LLM (~5 GB) into a volume. Then:

```bash
curl http://localhost:8000/health           # {"status":"ok",...}
docker compose exec backend python -m scripts.create_admin \
    --email admin@yourhospital.bd --password '<password>'
```

- Admin console: `http://localhost:3000` · Patient portal: `http://localhost:3000/portal`
- Voice worker (browser calls + SIP) is an opt-in profile:
  `docker compose --profile voice up --build -d voice`
- Every host port is overridable via `.env` (`BACKEND_PORT`, `UI_PORT`,
  `POSTGRES_PORT`, `OLLAMA_HOST_PORT`), and `DOCKER_OLLAMA_URL` lets the
  containers reuse an Ollama already running on the host — so the stack can
  run alongside a local dev setup.
- `make up` / `make logs` / `make down` / `make migrate` wrap the common commands.

Full guide — production checklist (HTTPS, webhooks, backups, scaling, GPU),
dev-coexistence ports, and updating a deployment:
**[docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)**.

---

## Setup in detail

### 1. PostgreSQL

```bash
sudo service postgresql start
```

Create the (empty) database. **Use credentials that actually work on your
machine** — the default `postgres/postgres` only works if you set that
password. Common options:

```bash
# Option A — you know the postgres password:
psql "postgresql://postgres:<password>@localhost:5432/postgres" -c "CREATE DATABASE appointments;"

# Option B — set a known password first (needs sudo):
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres createdb appointments

# Option C — create a role for your OS user:
sudo -u postgres psql -c "CREATE ROLE $USER LOGIN SUPERUSER PASSWORD 'changeme';"
createdb appointments
```

Put the **working DSN** in `.env` as `DATABASE_URL`, then apply the schema —
**Alembic is the single source of truth** (`db/schema.sql` is a stale
leftover from the original single-tenant MVP; don't use it):

```bash
alembic upgrade head
```

Verify:

```bash
psql "$DATABASE_URL" -c "SELECT day_of_week, start_time, end_time FROM doctor_schedule ORDER BY day_of_week;"
```

LangGraph checkpoint/store tables and the pgvector extension (for RAG) are
created automatically on first backend start.

### 2. Ollama

```bash
ollama serve
ollama pull gemma4:latest        # default model — good multi-turn Bangla + tool-calling
ollama list                      # verify
```

`OLLAMA_MODEL` is swappable in `.env` with no code change — but the model
must be reliable at **multi-turn Bangla tool-calling**, not just Bangla
fluency (the agent is a tool-calling ReAct loop, see
[ARCHITECTURE.md §2.4](./ARCHITECTURE.md#24-the-langgraph-agent--a-bound-tool-calling-react-loop)).
Smaller/weaker models can produce fluent Bangla prose while still failing to
call tools correctly across a multi-turn booking flow — test the full booking
flow, not just a single reply, before adopting a different model.

### 3. Python backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env           # edit DATABASE_URL (and OLLAMA_MODEL if desired)
```

---

## Running the FastAPI backend

```bash
source .venv/bin/activate
uvicorn api.app:app --reload --port 8000
```

- Interactive docs: **http://localhost:8000/docs**
- Health check: `curl http://localhost:8000/health`

On startup it builds the LangGraph agent (creating checkpoint tables) and warms
the database pool.

---

## Talking to the agent

`session_id` is the LangGraph `thread_id` and must be **8–128 alphanumeric
characters** (hyphens/underscores allowed) — get a safe one from
`POST /chat/session` rather than inventing your own (predictable IDs let one
caller read/inject into another's conversation).

```bash
# Get a session id
SID=$(curl -s -X POST localhost:8000/chat/session | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

# Open the call — returns the Bangla greeting
curl -s -X POST localhost:8000/chat/start \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"\"}"

# Then send the patient's replies, one per call
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"আমার একটা অ্যাপয়েন্টমেন্ট লাগবে\"}"

curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"আমার নাম রাহেলা বেগম, বয়স ৪২, নম্বর ০১৭১১২৩৪৫৬৭\"}"
```

Each response is JSON:

```json
{ "reply": "…বাংলা…", "appointment_id": null, "patient_name": "…", "done": false }
```

`done: true` means the appointment was booked; `appointment_id` holds the new
row's UUID. Unlike the original single-tenant MVP, there's no fixed
`phase`-by-phase script to follow — the agent is a tool-calling loop (see
[ARCHITECTURE.md §2.4](./ARCHITECTURE.md#24-the-langgraph-agent--a-bound-tool-calling-react-loop))
and will ask for whatever's missing, search doctors, offer slots, or answer a
question, in whatever order the conversation actually goes.

**Streaming (WebSocket):** connect to `ws://localhost:8000/chat/ws`, send
`{"session_id": "...", "message": "..."}`, and receive `{"type":"token","text":…}`
events followed by a final `{"type":"end", ...}`.

**Other channels** (WhatsApp, SMS, browser voice, SIP telephony) drive the
same agent over a different transport — see
[WhatsApp/SMS](./docs/WHATSAPP_AND_SMS.md), [Telephony](./docs/TELEPHONY.md),
and [Browser voice](./docs/VOICE_WEB.md).

---

## REST API

The full surface is large (multi-tenant admin, patient portal, payments,
platform-admin, RAG documents, reviews, escalations, ...) — **interactive
docs at `http://localhost:8000/docs`** are the source of truth. Route files,
one per concern, with a one-line purpose each:
[ARCHITECTURE.md §2.2](./ARCHITECTURE.md#22-the-fastapi-layer-api).

The original single-tenant core, unchanged in shape:

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat/session` | Get a fresh, safe `session_id` |
| POST | `/chat/start` | Open a call; returns the greeting |
| POST | `/chat` | Send one patient message; get the reply |
| WS | `/chat/ws` | Streaming chat |
| GET | `/appointments` | List; `?date_from=&date_to=&status=&q=` (staff, JWT) |
| PATCH | `/appointments/{id}` | Body `{"status":"cancelled"}` (staff, JWT) |
| GET | `/availability` | Open slots; `?days_ahead=7` |
| GET | `/schedule` | Weekly schedule (staff, JWT) |
| PUT | `/schedule` | Replace the weekly schedule (staff, JWT) |
| GET | `/health` | Liveness |

Most non-chat endpoints now require a staff or patient JWT
(`POST /auth/login`, `POST /patient/login`) — see
[ARCHITECTURE.md §7.4](./ARCHITECTURE.md#74-roles-and-what-they-can-access)
for roles and scope.

---

## Admin UI

The Next.js app is **fully API-driven** — it talks only to the FastAPI
backend (`NEXT_PUBLIC_API_URL`), never to Postgres directly.

```bash
cd appointment-ui
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL to the backend
npm run dev                         # http://localhost:3000
```

It's one app, three audiences:

- **Hospital staff console** — dashboard, appointments, schedule, patients,
  queue, conversations/escalations, integrations (channel mapping), reports,
  audit log, settings (doctors, reviews moderation). JWT staff login at `/login`.
- **Platform-admin dashboard** (`/platform`, login at `/platform-admin`) —
  revenue, hospital billing, payment ledger. See
  [docs/MONETIZATION.md](./docs/MONETIZATION.md).
- **Patient portal** (`/portal`) — a doctor-marketplace UX: browse/search
  hospitals & doctors, view a doctor's profile (fees, rating, reviews),
  book (with a pay step when the hospital charges a booking fee), manage
  appointments, and an account/subscription page. Patient login/signup at
  `/portal/login` / `/portal/signup`; hospital self-signup at `/signup`. Bangla/
  English UI toggle (agent replies always follow the patient's own language).

See [ARCHITECTURE.md §3](./ARCHITECTURE.md#3-the-nextjs-app-appointment-ui)
for the full page/file map.

---

## Monetization & billing

The platform earns two ways, both configurable and off-by-default-friendly (set
fees to 0 to run entirely free):

- **Patients** pay a per-booking fee through an online gateway *before* the slot
  confirms — **or** a monthly premium subscription that waives booking fees. Every
  new patient gets a 30-day full-access trial; after it lapses the free tier keeps
  direct booking but loses AI voice calls, is capped to a few AI (chat/voice)
  bookings per month, sees a limited history, and stops getting SMS reminders.
  **Calling the platform phone number** is the same premium perk over PSTN:
  patients verify their number once (SMS OTP on `/portal/account`) and are then
  recognized by caller-ID — see
  [docs/TELEPHONY.md](docs/TELEPHONY.md#who-can-call-the-premium-gate).
- **Hospitals** pay a monthly subscription. Self-signup (`/signup`) grants a free
  first month; a lapsed hospital moves `active → past_due → suspended` and, once
  suspended, its doctors disappear from patient search/browse/booking (patients are
  never blocked — only the hospital is).

A **platform-admin dashboard** at `/platform` (JWT `platform_admin` role) shows
booking-fee + subscription revenue, per-hospital billing with a "mark subscription
paid" action, and a payment ledger with manual-confirm and refund-flag actions.

The default `PAYMENT_PROVIDER=manual` needs no gateway account — it either
auto-confirms (dev) or shows a pay-at-desk page an admin marks paid.
`PAYMENT_PROVIDER=sslcommerz` uses the free BD sandbox gateway (aggregates
bKash/Nagad/cards). See **[docs/MONETIZATION.md](docs/MONETIZATION.md)** for the
full model, gateway setup, and the API surface.

---

## Environment variables

**`.env.example` is the authoritative full list** (~65 settings, grouped and
commented, mirroring `config.py` exactly) — copy it and edit. The table below
is only the ones worth knowing about before you first run the stack; anything
niche (Gemini cloud fallbacks, LiveKit Inference, PII encryption, rate
limiting, HIS webhooks, ...) is documented inline in `.env.example`.

### Backend — `.env`

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/appointments` | Postgres DSN (data + checkpoints + RAG vectors) |
| `JWT_SECRET` | *(insecure default — warns on startup)* | Staff/patient login signing key. **Set a strong random value before deploying.** |
| `PLATFORM_ADMIN_KEY` | `""` | Required to provision new hospital tenants via `POST /clinics`. Empty = disabled. |
| `LLM_PROVIDER` | `ollama` | `ollama` (local LLM on) · `openrouter` (cloud, OpenAI-compatible) · `gemini` (cloud). Cloud providers send conversation PII off-box; both fall back to Ollama if their key is unset. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `gemma4:latest` | Local LLM server + model |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | `""` / `google/gemma-4-31b-it` | OpenRouter key + model id (used when `LLM_PROVIDER=openrouter`) |
| `OLLAMA_NUM_CTX` | `8192` | Context window; don't go below this or long tool-calling threads truncate |
| `RAG_BACKEND` | `pgvector` | Vector store for hospital-document Q&A (`pgvector` or legacy `chroma`) |
| `STT_ENGINE` / `TTS_ENGINE` | `whisper` / `mms` | Voice engines — local by default; `gemini` or `livekit` for cloud |
| `VOICE_FALLBACK_SCOPE` | `platform` | Unmapped inbound calls (the platform number) run the cross-hospital platform agent; `default_clinic` = legacy behavior |
| `VOICE_PREMIUM_GATE` | `true` | Platform-number calls only for premium/trial patients matched by one-time-verified caller-ID; set `false` for `main.py console` dev |
| `AVAILABILITY_DAYS_AHEAD` | `7` | How far ahead to offer slots |
| `STRICT_CHANNEL_ROUTING` | `false` | Reject messages from unmapped channel identifiers instead of falling back to the default clinic — turn on for real multi-tenant deployments |
| `PATIENT_ENCRYPTION_KEY` | `""` | Fernet key for patient PII at rest — set for any real (non-dev) deployment |
| `PAYMENT_PROVIDER` | `manual` | `manual` (no gateway — auto-confirm or pay-at-desk) or `sslcommerz` |
| `CORS_ORIGINS` | `http://localhost:3000,...` | Browser origins allowed to call the API |

Payments/billing/plan-tier variables (`PAYMENT_*`, `*_SUBSCRIPTION_FEE`,
`*_TRIAL_DAYS`, `FREE_*`, `SSLCOMMERZ_*`, `PUBLIC_BASE_URL`,
`PORTAL_BASE_URL`) are covered in full in
**[docs/MONETIZATION.md](docs/MONETIZATION.md)**; WhatsApp/SMS/Twilio
variables in **[docs/WHATSAPP_AND_SMS.md](docs/WHATSAPP_AND_SMS.md)**; voice
engine variables in **[docs/VOICE_WEB.md](docs/VOICE_WEB.md)**; scaling
variables (`REDIS_URL`, `DB_POOL_*`) in **[docs/SCALING.md](docs/SCALING.md)**.

### UI — `appointment-ui/.env.local`

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | Backend base URL. Baked in at build time — docker builds must set the **public** URL before `next build` |
| `NEXT_PUBLIC_API_KEY` | Only used by the legacy standalone `/chat` demo page; leave empty otherwise |

---

## Command reference

```bash
# Services
sudo service postgresql start
ollama serve
ollama pull gemma4:latest

# Database (schema is owned by Alembic — never db/schema.sql)
createdb appointments
alembic upgrade head
psql "$DATABASE_URL" -c "SELECT patient_name, scheduled_at, status FROM appointments ORDER BY created_at DESC LIMIT 10;"

# Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.app:app --reload --port 8000

# UI
cd appointment-ui && npm install && npm run dev

# Reset DB (DANGER: drops ALL data — appointments, tenants, patients, payments, ...)
dropdb appointments && createdb appointments && alembic upgrade head
```

Also see the `Makefile` (`make up`/`down`/`logs`/`migrate`/`create-admin`/`test`)
for the Docker-based equivalents.

---

## Conversation flow

The agent is a **tool-calling ReAct loop**, not a fixed script: one small
graph (`call_model → tools → call_model | post_booking`) where the LLM itself
decides which tool to call — search doctors, list slots, book, check hospital
info (RAG), list/cancel/reschedule an existing booking, or escalate to a
human — and Python only decides when the turn ends. There's no fixed phase
order to follow; the conversation can go straight to booking, detour through
a question, or come back later to reschedule, all in the same thread.

Patient details (`{name, age, mobile}`) are still captured with Bangla
numerals normalised, and a long digit run in the message is preferred
deterministically for the phone number so digits don't get dropped/transposed
by the model. See
[ARCHITECTURE.md §2.4](./ARCHITECTURE.md#24-the-langgraph-agent--a-bound-tool-calling-react-loop)
for the full design, including why the tool set bound to the LLM changes by
session (and why that binding must never change mid-thread).

---

## Voice (local mic)

Voice is **fully implemented**, not experimental — see
[ARCHITECTURE.md §8](./ARCHITECTURE.md#8-voice) for the full picture (browser
calls via the patient portal, SIP/DID telephony, and this local-mic mode).
This section covers only the quickest way to try it: talking to the agent
through your laptop mic/speakers via LiveKit's **console mode**, with local
STT/TTS. Implemented in `voice/` and wired in `main.py`.

### Setup

```bash
# python deps (voice extras — STT/TTS/LiveKit agent runtime)
pip install -r requirements-voice.txt
```

Default engines are local: `STT_ENGINE=whisper` (faster-whisper) and
`TTS_ENGINE=mms` (facebook/mms-tts-ben, a real neural Bangla voice — no
external TTS binary to install). See `.env.example` for every STT/TTS option,
including `espeak` (robotic, zero download) and cloud fallbacks
(`gemini`, `livekit`).

### Run

```bash
# Postgres + Ollama must be running (same as the backend)
python main.py console      # then speak Bangla; press Ctrl+C to quit
```

First run downloads the Whisper and TTS model weights.

### Quality notes
- **STT accuracy**: `WHISPER_MODEL=medium` is the default; `large-v3` is
  markedly better for Bangla if you have the CPU/GPU budget for it.
- **TTS**: `mms` is natural and CPU-fast; `TTS_PITCH_SEMITONES` can deepen the
  voice. `parler` sounds better still but is too slow on CPU-only hardware.
- Each `console` run uses a fresh conversation (new checkpoint thread).
- For a browser or phone experience instead of the local mic, see
  [docs/VOICE_WEB.md](./docs/VOICE_WEB.md) / [docs/TELEPHONY.md](./docs/TELEPHONY.md).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `password authentication failed for user "postgres"` | The `postgres` role's password isn't `postgres`. Use working credentials in `DATABASE_URL` (see [PostgreSQL setup](#1-postgresql)) |
| `connection refused` to Postgres | Postgres not running, or wrong host/port in `DATABASE_URL` |
| `alembic upgrade head` fails / DB missing tables | You're on an old checkout or ran `db/schema.sql` instead — that file is stale; always use `alembic upgrade head` |
| `400 session_id must be 8–128 ...` | Get a session id from `POST /chat/session` rather than inventing a short one |
| Backend hangs / `/chat` slow | First model load is slow on CPU; subsequent turns are faster. Ensure `ollama serve` is running and the model is pulled |
| Empty replies | Ensure `OLLAMA_MODEL` exists (`ollama list`); the agent already sends a human turn so a current model should respond |
| Agent doesn't call tools reliably / ignores instructions | Not every model that's fluent in Bangla is reliable at multi-turn tool-calling — try `gemma4:latest` (the tested default) before assuming it's a bug |
| UI shows no data / "Failed to fetch" | `NEXT_PUBLIC_API_URL` must point at the running backend, and the UI's origin must be in the backend's `CORS_ORIGINS` |
| `401`/`403` on most endpoints | Most non-chat endpoints need a staff/patient JWT now — log in via `/login` or `/portal/login` first, or `POST /auth/login` directly |
| Voice: no audio device in `console` | Console needs a real mic/speaker; run it on your local machine, not over SSH |
| Voice: STT mishears Bangla | Raise `WHISPER_MODEL` to `large-v3` in `.env` |

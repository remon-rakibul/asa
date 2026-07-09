# Doctor Appointment Setter Agent (Bangla, fully local)

A doctor's-clinic appointment-setting assistant. It converses in **Bangla**, reads
the doctor's available times from a Postgres-backed schedule, collects the
patient's name / age / mobile, books the appointment, and stores it. A **FastAPI
backend** runs the agent over text (HTTP + WebSocket); a **Next.js admin UI** lets
clinic staff manage the schedule and view/cancel appointments.

Everything runs locally — **no cloud LLM, no Google Calendar, no OAuth.**

> Architecture deep-dive: **[ARCHITECTURE.md](./ARCHITECTURE.md)**.
> Two ways to use the agent: the **FastAPI backend** (text, fully supported) and
> **local voice** via your mic (`python main.py console`) — see
> [Voice (local mic)](#voice-local-mic).

---

## Table of Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Quick start](#quick-start)
4. [Setup in detail](#setup-in-detail)
5. [Running the FastAPI backend](#running-the-fastapi-backend)
6. [Talking to the agent](#talking-to-the-agent)
7. [REST API](#rest-api)
8. [Admin UI](#admin-ui)
9. [Environment variables](#environment-variables)
10. [Command reference](#command-reference)
11. [Conversation flow](#conversation-flow)
12. [Voice (local mic)](#voice-local-mic)
13. [Troubleshooting](#troubleshooting)

---

## Architecture

```
Patient (text now / voice later)
        │
        ▼
FastAPI backend  ──  /chat, /chat/ws, /appointments, /schedule
        │
        ▼
LangGraph state machine  ──ChatOllama──▶  Ollama (llama3.2:3b)
        │
        ▼
PostgreSQL  ◀── appointments + doctor_schedule + LangGraph checkpoints ──▶ Next.js UI
```

- The **LLM only** generates Bangla replies and extracts fields. Control flow
  (what to ask next, when to book) is deterministic Python.
- Each turn is **one graph invocation**; the Postgres checkpointer restores
  conversation state between turns (keyed by `session_id`).
- Availability is **computed** from `doctor_schedule` minus confirmed
  `appointments` — no calendar service.

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

```bash
# 1. Services
sudo service postgresql start
ollama serve &                 # if not already running
ollama pull llama3.2:3b

# 2. Database (see notes below if the postgres password differs)
createdb appointments
psql "$DATABASE_URL" -f db/schema.sql     # DATABASE_URL = your Postgres DSN

# 3. Python backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit DATABASE_URL to match your Postgres

# 4. Run
uvicorn api.app:app --reload --port 8000
# open http://localhost:8000/docs
```

Then in another terminal, start a conversation (see
[Talking to the agent](#talking-to-the-agent)).

---

## Setup in detail

### 1. PostgreSQL

```bash
sudo service postgresql start
```

Create the database and apply the schema. **Use credentials that actually work on
your machine** — the default `postgres/postgres` only works if you set that
password. Common options:

```bash
# Option A — you know the postgres password:
psql "postgresql://postgres:<password>@localhost:5432/postgres" -c "CREATE DATABASE appointments;"
psql "postgresql://postgres:<password>@localhost:5432/appointments" -f db/schema.sql

# Option B — set a known password first (needs sudo):
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres createdb appointments
psql "postgresql://postgres:postgres@localhost:5432/appointments" -f db/schema.sql

# Option C — create a role for your OS user:
sudo -u postgres psql -c "CREATE ROLE $USER LOGIN SUPERUSER PASSWORD 'changeme';"
createdb appointments
psql "postgresql://$USER:changeme@localhost:5432/appointments" -f db/schema.sql
```

Put the **working DSN** in `.env` as `DATABASE_URL`. Verify:

```bash
psql "$DATABASE_URL" -c "SELECT day_of_week, start_time, end_time FROM doctor_schedule ORDER BY day_of_week;"
```

The schema seeds Mon–Fri 09:00–17:00, 30-minute slots. LangGraph checkpoint
tables are created automatically on first backend start.

### 2. Ollama

```bash
ollama serve
ollama pull llama3.2:3b        # default model
ollama list                    # verify
```

`llama3.2:3b`'s Bangla is usable but imperfect. For better quality set
`OLLAMA_MODEL` in `.env` to a stronger multilingual model you've pulled, e.g.
`ollama pull qwen2.5:3b` then `OLLAMA_MODEL=qwen2.5:3b`.

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

Use a stable `session_id` per conversation (it's the LangGraph `thread_id`).

```bash
# Open the call — returns the Bangla greeting
curl -s -X POST localhost:8000/chat/start \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"call-1","message":""}'

# Then send the patient's replies, one per call
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"call-1","message":"আমার নাম রাহেলা বেগম"}'

curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"call-1","message":"আমার বয়স ৪২ বছর"}'

curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"call-1","message":"আমার নম্বর ০১৭১১২৩৪৫৬৭"}'

curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"call-1","message":"প্রথমটা দিন"}'

curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"call-1","message":"হ্যাঁ ঠিক আছে"}'
```

Each response is JSON:

```json
{ "reply": "…বাংলা…", "phase": "confirm_slot", "appointment_id": null,
  "patient_name": "…", "done": false }
```

`done: true` (phase `farewell`) means the appointment was booked;
`appointment_id` holds the new row's UUID.

**Streaming (WebSocket):** connect to `ws://localhost:8000/chat/ws`, send
`{"session_id": "...", "message": "..."}`, and receive `{"type":"token","text":…}`
events followed by a final `{"type":"end", ...}`.

---

## REST API

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat/start` | Open a call; returns the greeting |
| POST | `/chat` | Send one patient message; get the reply |
| WS | `/chat/ws` | Streaming chat |
| GET | `/appointments` | List; `?date_from=&date_to=&status=&q=` |
| PATCH | `/appointments/{id}` | Body `{"status":"cancelled"}` |
| GET | `/availability` | Open slots; `?days_ahead=7` |
| GET | `/schedule` | Weekly schedule |
| PUT | `/schedule` | Replace the weekly schedule |
| GET | `/health` | Liveness |

```bash
curl "localhost:8000/appointments?status=confirmed"
curl "localhost:8000/availability?days_ahead=3"
curl -X PATCH localhost:8000/appointments/<uuid> \
  -H 'Content-Type: application/json' -d '{"status":"cancelled"}'
```

---

## Admin UI

The Next.js dashboard reads/writes the same database directly.

```bash
cd appointment-ui
npm install
cp .env.local.example .env.local   # set DATABASE_URL to the SAME database
npm run dev                         # http://localhost:3000
```

Pages: **Dashboard** (`/`), **Appointments** (`/appointments`),
**Schedule** (`/schedule`).

---

## Environment variables

### Backend — `.env`

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/appointments` | Postgres DSN (data + checkpoints) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model name |
| `OLLAMA_TEMPERATURE` | `0.3` | Sampling temperature |
| `AVAILABILITY_DAYS_AHEAD` | `7` | How far ahead to offer slots |

(`WHISPER_MODEL` / `STT_LANGUAGE` / `WHISPER_DEVICE` and `LIVEKIT_*` are used by
the local voice path. `PIPER_*` is unused — TTS uses espeak-ng.)

### UI — `appointment-ui/.env.local`

| Variable | Description |
|---|---|
| `DATABASE_URL` | Same Postgres DSN as the backend |

---

## Command reference

```bash
# Services
sudo service postgresql start
ollama serve
ollama pull llama3.2:3b

# Database
createdb appointments
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -c "SELECT patient_name, scheduled_at, status FROM appointments ORDER BY created_at DESC LIMIT 10;"

# Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.app:app --reload --port 8000

# UI
cd appointment-ui && npm install && npm run dev

# Reset DB (DANGER: drops data + checkpoints)
psql "$DATABASE_URL" -c "DROP TABLE IF EXISTS appointments, doctor_schedule, checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations CASCADE;"
psql "$DATABASE_URL" -f db/schema.sql
```

---

## Conversation flow

```
greeting → collect_info → fetch_slots → present_slots → confirm_slot → book_appointment → farewell
```

The LLM extracts `{name, age, mobile}` (Bangla numerals normalised; a long digit
run in the message is captured deterministically so phone numbers stay exact) and
generates Bangla replies. Routing between phases is deterministic Python, and the
patient's reply each turn is interpreted in the context of the saved phase. See
[ARCHITECTURE.md](./ARCHITECTURE.md) for details.

---

## Voice (local mic)

Talk to the agent through your laptop mic/speakers via LiveKit's console mode. The
pipeline is **Silero VAD → faster-whisper STT → the LangGraph agent → espeak-ng
TTS**, all local. Implemented in `voice/` (`whisper_stt.py`, `langgraph_llm.py`,
`espeak_tts.py`) and wired in `main.py`.

### Setup

```bash
# system package for Bangla TTS
sudo apt install -y espeak-ng

# python deps (already in requirements.txt; installs faster-whisper etc.)
pip install faster-whisper livekit-agents livekit-plugins-silero
```

No Piper download is needed — Piper has no Bengali voice, so TTS uses espeak-ng's
`bn` voice.

### Run

```bash
# Postgres + Ollama must be running (same as the backend)
python main.py console      # then speak Bangla; press Ctrl+C to quit
```

First run downloads the Whisper model and the Silero VAD weights.

### Quality notes
- **espeak-ng TTS is robotic** (it's the only reliable offline Bangla voice). It's
  fine for testing; for natural speech you'd swap in a cloud/better Bangla TTS.
- **STT accuracy**: the default `WHISPER_MODEL=base` is weak for Bangla. Set
  `WHISPER_MODEL=small` (or `large-v3-turbo` if you have a GPU / patience) in
  `.env` for much better recognition of real speech.
- Each `console` run uses a fresh conversation (new checkpoint thread).

> Status: the pipeline is built and every component is verified (STT, TTS, LLM
> wrappers, and `AgentSession` assembly). Live mic capture depends on your audio
> devices and can't be exercised headlessly.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `password authentication failed for user "postgres"` | The `postgres` role's password isn't `postgres`. Use working credentials in `DATABASE_URL` (see [PostgreSQL setup](#1-postgresql)) |
| `connection refused` to Postgres | Postgres not running, or wrong host/port in `DATABASE_URL` |
| Backend hangs / `/chat` slow | First model load is slow on CPU; subsequent turns are faster. Ensure `ollama serve` is running and the model is pulled |
| Empty replies | Ensure `OLLAMA_MODEL` exists (`ollama list`); the agent already sends a human turn so a current model should respond |
| Agent replies in English | Use a stronger multilingual model via `OLLAMA_MODEL` (e.g. `qwen2.5:3b`) |
| UI shows no data | `appointment-ui/.env.local` `DATABASE_URL` must match the backend's database |
| Voice: `espeak-ng: not found` | Install it: `sudo apt install -y espeak-ng` |
| Voice: STT mishears Bangla | Raise `WHISPER_MODEL` to `small` or `large-v3-turbo` in `.env` |
| Voice: no audio device in `console` | Console needs a real mic/speaker; run it on your local machine, not over SSH |

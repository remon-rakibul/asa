# ASA — Project & Session Context

_Last updated: **2026-10-30**. Written so a session on any machine can pick up
instantly. Read **Part A** to resume; Part B is reference._

ASA is a **Bangla AI doctor-appointment platform for Bangladesh**: one patient
portal, one WhatsApp number and one phone line for every hospital on the
platform. Patients search doctors across hospitals, ask questions (RAG over
hospital documents) and book by chat, WhatsApp text, WhatsApp voice notes,
WhatsApp calls, browser voice or phone. Hospitals self-sign-up, manage
doctors/schedules in an admin console, and pay via subscription + prepaid
credits. Repo: `github.com/remon-rakibul/asa` (branch `main`).

---

# PART A — CURRENT STATE

## 1. Status at a glance — 2026-10-30

| Area | State |
|---|---|
| Web chat + patient portal | ✅ Working (streamed replies, unified per-account thread) |
| Browser voice (LiveKit) | ✅ Working |
| **WhatsApp chat** | ✅ Working on Meta **test number** +1 555 665 7143 |
| **WhatsApp voice notes** | ✅ Transcribed (Gemini) → agent replies in text |
| **WhatsApp calls — ASA calls patient** | ✅ Verified 2026-10-30 (rang, answered, agent spoke) |
| **WhatsApp calls — patient calls ASA** | ⚠️ Worked 2026-10-28; since Meta **reset the test number** (10/28–29) WhatsApp hides the call button. Calling is `ENABLED` in Meta — out of our control on a test number |
| Phone line (PSTN/SIP) | ❌ Not provisioned — no BD SIP trunk/DID yet (code + `scripts/provision_sip.py` ready) |
| SMS | ❌ `SMS_PROVIDER=none` — OTPs/reminders/confirmations not sent; BD vendor not chosen |
| Payments | `manual` provider (SSLCommerz code exists, not live) |
| Patient PII encryption | ✅ On (`PATIENT_ENCRYPTION_KEY` set, all old rows backfilled) |
| Tests | ✅ 551 Python (`make test`), 61 vitest (`make test-frontend`) |

Latest commits: `237200c` outbound WhatsApp calls · `b0f8383` WhatsApp
registration watchdog · `976ab9d` encryption docs · `6e87b0a` PII backfill ·
`04173da` WhatsApp calls + ngrok in Docker + fast builds · `fcaa263`
production integrations (outbox, platform-mode messaging, SIP provisioning,
live transfer).

## 2. Run it

```bash
make up            # build + start everything (docker compose up --build -d)
make down          # stop everything
make verify        # live check of every integration + config warnings
make logs
```

`.env` has `COMPOSE_PROFILES=voice,tunnel`, so `make up` starts **postgres,
redis, backend, voice, ui and ngrok**. Everything auto-restarts with Docker.

| Service | URL |
|---|---|
| Admin console / patient portal (UI) | http://localhost:3001 (`/portal` for patients) |
| Backend API | http://localhost:8001 (`/docs`, `/health`, `/health/live`) |
| Public webhook URL (ngrok) | https://aviation-effort-darling.ngrok-free.dev |
| ngrok request inspector | http://127.0.0.1:4040 |

Rebuilds are cached (BuildKit pip cache, layered voice image): a no-change
rebuild takes seconds. Rebuild only what changed:
`docker compose up -d --build backend`.

## 3. Current configuration (no secret values)

- **LLM:** OpenRouter `google/gemma-4-31b-it` (`LLM_PROVIDER=openrouter`; falls back to local Ollama if the key is missing). Embeddings: Ollama `nomic-embed-text` on the host.
- **Voice:** LiveKit Cloud; STT `speechmatics/enhanced`, TTS `cartesia/sonic-3` via LiveKit Inference (`STT_ENGINE=TTS_ENGINE=livekit`). Agent name `appointment-setter`.
- **WhatsApp:** phone number id `1171444326050003`, WABA `4602038116741480`, Meta app **asa** (`1008465318593658`) in business portfolio **Asa** (`1377304637878729`); system user `remon-rakibul` with a permanent token. Webhook: `<ngrok>/whatsapp/webhook`, fields `messages` + `calls` (v25.0). Calling `ENABLED`.
- **Ports:** `BACKEND_PORT=8001`, `UI_PORT=3001`. `WEB_CONCURRENCY=4` + Redis rate limiting.
- **Credits/billing:** `CREDITS_ENABLED=true`; SMS metered per segment (Bangla = UCS-2, 70/67 chars).

### Where secrets live (never commit, never paste in chat)
- **`SECRETS.local.md`** (project root, gitignored, chmod 600) — **every secret in one place**: DB, JWT, encryption key, OpenRouter/Gemini, LiveKit, WhatsApp token/app secret/verify token/PIN, ngrok, Twilio, plus Meta account IDs. Generated from `.env`; regenerate it after changing `.env`.
- `.env` — the source of truth for all keys/tokens (gitignored).
- `whatsapp_token.txt` (project root, gitignored, chmod 600) — copy of WhatsApp token, app secret, IDs, registration PIN.
- `backups/` (gitignored) — DB dump taken before encryption (**plaintext** — delete when no longer needed).
- **Back up `PATIENT_ENCRYPTION_KEY` outside the server.** Lost key = unrecoverable patient data. Never change it.

## 4. WhatsApp — how it works now

- **Inbound text/voice note** → `/whatsapp/webhook` (HMAC-verified with `WHATSAPP_APP_SECRET`, de-duplicated) → `api/messaging.py` routing: ASA number = **platform mode**; sender with a **verified phone** joins their account thread `pt-acc{id}-platform`; unknown sender gets `whatsapp-<number>`; a hospital's own number (mapped in `channels`) keeps its clinic. Per-sender lock serialises turns.
- **Outbound** messages go through the **outbox** (`tools/outbox.py`): inline attempt, retries with backoff, TTL, idempotency, delivery receipts update status.
- **Registration watchdog:** Meta test numbers drop to `PENDING` ("Account not registered", WhatsApp dead both ways). The backend checks at startup + every 30 min and re-registers with `WHATSAPP_REGISTRATION_PIN`. Manual: `make whatsapp-register [FORCE=1]`. `make verify` fails if the number isn't registered.
- **Calls (LiveKit WhatsApp Connector):**
  - Patient → ASA: `calls` connect webhook → `AcceptWhatsAppCall` → voice agent dispatched with scope metadata; terminate → `DisconnectWhatsAppCall`.
  - ASA → patient: `docker compose exec backend python -m scripts.whatsapp_call_patient 8801XXXXXXXXX` → `DialWhatsAppCall`; when answered Meta sends a `BUSINESS_INITIATED` connect → `ConnectWhatsAppCall`. Needs the patient's call permission (test user +8801711309969 has temporary permission until ~2026-11-04).
  - A call continues the caller's WhatsApp/account thread. Premium gate doesn't apply. No SIP transfer on WhatsApp calls.
- **Allowed test recipient:** +8801711309969 (the developer's phone). The app is in development mode — only test recipients can message the test number.

## 5. Open items / next steps

**Needs the owner (Phase 0 — procurement):**
1. **Real WhatsApp business number + Meta Business verification** — fixes the hidden call button and test-number resets. +8801832296128 was considered on 2026-10-30, then **dropped by the owner** ("the old number is good enough"); the add-number form was closed without submitting.
2. **BD SMS vendor** (SSL Wireless / BulkSMSBD / Alpha SMS) with masked sender "ASA" + a two-way number → then build the vendor adapter, DLR + inbound routes.
3. **BD SIP provider (IPTSP) + DID** with REFER + caller-ID → run `scripts/provision_sip.py`, real call-quality tuning, load test.
4. **WhatsApp templates** (Bangla): booking_confirmation, appointment_reminder (১/২ buttons), payment_link, otp — needed to message patients outside the 24 h window.
5. **SSLCommerz live store**, a fixed **public domain** (replace ngrok in production).

**Can build without the owner:**
- "Call me" flow: patient asks on WhatsApp → ASA sends a call-permission request → dials them.
- WhatsApp-first-then-SMS `notify()`, interactive list messages for slots, payment link in-channel.
- Integrations admin page (live verify + outbox stats), alerts, consent text.
- Voice turn-taking: one "transcript arrived after turn committed" warning seen — consider raising `min_endpointing_delay`.

## 6. Gotchas learned

- `pkill -f "ngrok http"` kills your own shell if the pattern is in the command line — use `pkill -f '^ngrok http'`.
- `.env` isn't shell-sourceable (unquoted spaces) — read single keys with `grep '^KEY=' .env | cut -d= -f2-`.
- Tests load the real `.env`; `tests/conftest.py` pins the LLM to Ollama and disables the WhatsApp watchdog so tests never call cloud APIs.
- Meta `/register` often exceeds 5 s — use a long timeout and re-check status.
- Transient DNS failures inside containers happen right after the host wakes; they recover on their own.
- Commits are authored by **remon-rakibul only — no `Co-Authored-By` trailer**. Commit/push only when asked. Never commit `.env`, `whatsapp_token.txt`, `backups/`, `.claude/settings.local.json`.

---

# PART B — REFERENCE

## 7. Architecture (short)

```
Patient channels: web portal (SSE chat) · browser voice · WhatsApp (text/voice note/call) · phone (SIP, pending) · SMS (pending)
        │
FastAPI backend (api/)  ── routes: chat, patient_portal, whatsapp, twilio_sms, voice, platform, hospitals, …
        │                    background loops: reminders+billing (hourly), payment sweep (60 s),
        │                    outbox drain (10 s), WhatsApp registration watchdog (30 min)
        ▼
LangGraph agent (agent/)  ── tools: search_doctors, availability, book/cancel/reschedule, RAG, human help
        │                    checkpoints + long-term patient memory in Postgres
        ▼
Postgres + pgvector (32 Alembic migrations, head 0032) · Redis (rate limits)

Voice worker (main.py, LiveKit Agents) ── same LangGraph agent via voice/langgraph_llm.py;
   scope from dispatch metadata (portal / WhatsApp call) or SIP attributes (phone)
UI (appointment-ui/, Next.js) ── admin console, platform admin, patient portal
```

Key files: `api/messaging.py` (WhatsApp/SMS routing), `tools/outbox.py`,
`tools/whatsapp.py`, `tools/whatsapp_calls.py`, `tools/whatsapp_registration.py`,
`tools/integration_checks.py`, `tools/crypto.py`, `agent/runner.py`
(`run_message_turn`), `main.py` (voice worker), `scripts/` (verify, provision_sip,
whatsapp_register, whatsapp_call_patient, encrypt_existing_pii, seed_demo).

## 8. Docs index

| Doc | What |
|---|---|
| `README.md` | Overview, env var table |
| `docs/DEPLOYMENT.md` | Docker setup, production checklist |
| `docs/WHATSAPP_AND_SMS.md` | WhatsApp + SMS setup, re-registration |
| `docs/WHATSAPP_CALLING.md` | WhatsApp calls in/out, setup, troubleshooting |
| `docs/NGROK_TUNNEL.md` | Webhook tunnel run/stop (Docker + host) |
| `docs/TELEPHONY.md` | SIP trunk, provisioning, live transfer |
| `docs/ENCRYPTION.md` | PII encryption, key safety, backfill |
| `docs/MONETIZATION.md` | Fees, subscriptions, credits, SSLCommerz |
| `docs/SCALING.md`, `docs/VOICE_WEB.md`, `docs/GO_TO_MARKET_BD.md` | Scaling, browser voice, GTM |

## 9. Useful commands

```bash
make verify                       # integrations status
make tunnel-status                # public URL reachable?
make whatsapp-register            # fix "Account not registered"
make encrypt-pii                  # encrypt pre-key PII rows (idempotent)
docker compose exec backend python -m scripts.whatsapp_call_patient 8801XXXXXXXXX
docker compose exec backend python -m scripts.provision_sip --dry-run
docker compose exec backend python -m scripts.seed_demo
make shell-db                     # psql
```

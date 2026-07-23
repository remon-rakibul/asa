# Architecture

This document explains how the codebase is structured and why. It is the
companion to `README.md` (which covers setup and commands). Read this to
understand *how the pieces fit together* before changing code.

---

## 1. System overview

Three runtime components share one PostgreSQL database:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                     PostgreSQL (pgvector extension enabled)                  │
│  hospitals/clinics/channels/users │ patients/patient_accounts │ appointments │
│  payments/subscriptions │ doctor_reviews │ conversation_log/escalations      │
│  LangGraph checkpoints + store (patient memory) │ RAG document embeddings    │
└───────▲───────────────────────▲──────────────────────────────▲──────────────┘
        │ asyncpg + psycopg      │ postgres.js (via REST only)   │ asyncpg
┌───────┴─────────────────────┐ ┌┴────────────────────────────┐ ┌┴─────────────┐
│   FastAPI backend (api/)     │ │   Next.js app (appointment- │ │ LiveKit voice │
│  agent + full REST API for   │◄┤   ui/): staff console +     │ │ worker (voice/,│
│  staff console, patient      │ │   patient portal, fully     │ │ main.py)       │
│  portal, platform admin      │ │   API-driven (no direct DB) │ │ browser + SIP  │
└───────────────────────────────┘ └──────────────────────────────┘ └───────────────┘
```

- **The FastAPI backend** runs the conversational agent (Bangla text/voice
  in, Bangla text/voice out) over chat, WhatsApp, SMS, browser voice, and SIP
  telephony, and exposes the entire REST surface the Next.js app is built on
  — there is no direct-DB path from the frontend anymore.
- **The Next.js app** is one codebase with two audiences: a staff/admin
  console (schedule, appointments, patients, queue, escalations, reports,
  platform-admin dashboard) and a patient-facing marketplace portal
  (`/portal`) — both talk to the backend exclusively through `lib/api.ts`.
- **The voice worker** (LiveKit) is a separate long-running process reusing
  the same agent core (`agent/`, `tools/`) over a different transport. It is
  **fully implemented**, not experimental — see [§8 Voice](#8-voice).
- All three share the Postgres schema (owned by **Alembic**, see
  [§4](#4-the-shared-contract-the-database--the-rest-api)) and, for the
  backend and voice worker, the same LangGraph checkpointer/store.

---

## 2. The backend

### 2.1 Layered design

```
HTTP / WebSocket            api/app.py + api/routes/*        (transport)
      │
      ▼
Turn runner                 agent/runner.py                  (one turn ↔ one call)
      │
      ▼
LangGraph graph              agent/graph.py + router.py        (control flow)
      │                     + nodes.py + prompts.py + state.py
      ▼
Tools                       agent/tools.py                     (LLM-callable actions)
      ▼
Data layer                  tools/database.py, tools/rag.py,    (availability, booking,
                             tools/payments.py, agent/memory.py  payments, RAG, memory)
      ▼
PostgreSQL
```

| Layer | File(s) | Responsibility |
|---|---|---|
| Transport | `api/app.py`, `api/routes/*`, `api/schemas.py` | HTTP/WS endpoints, request/response models, auth |
| Turn runner | `agent/runner.py` | Drive the graph for one turn; collect spoken text + stream events |
| Orchestration | `agent/graph.py`, `agent/router.py` | Graph wiring + deterministic routing |
| Behaviour | `agent/nodes.py`, `agent/prompts.py` | Prompt assembly, tool binding, corrective retry |
| Tools | `agent/tools.py` | Everything the LLM can call: booking, search, RAG, escalation |
| State | `agent/state.py` | The typed conversation state |
| Data | `tools/database.py` | Multi-tenant schema, availability, booking, billing |
| RAG | `tools/rag.py` | Hospital-scoped (and cross-hospital) document search |
| Payments | `tools/payments.py` | Manual / SSLCommerz provider abstraction |
| Memory | `agent/memory.py` | Cross-session patient profile + visit history |
| Cross-cutting | `utils/text.py`, `config.py` | Voice-safe sanitisation, settings |

### 2.2 The FastAPI layer (`api/`)

`api/app.py` creates the `FastAPI` app with a **lifespan** that, on startup,
builds the compiled graph once (`build_graph()`, which also creates the
Postgres checkpoint/store tables) and warms the asyncpg pool; on shutdown it
closes the pool. The compiled graph is stored on `app.state.graph`. A
background sweep loop (piggybacking the reminder loop) advances hospital
billing state and expires stale payment holds. CORS allows the Next.js
origin(s) (`CORS_ORIGINS`).

Routers, one file per concern (`api/routes/`):

| Router | Purpose |
|---|---|
| `auth.py` | Staff login/JWT (`POST /auth/login`) |
| `chat.py` | Agent endpoints — `POST /chat/start`, `POST /chat`, `WS /chat/ws` |
| `appointments.py`, `schedule.py` | Staff-facing appointment/schedule CRUD |
| `doctors.py` | Doctor roster, fees, photo |
| `reviews.py` | Staff moderation of patient reviews (hide/republish) |
| `hospitals.py` | Tenant provisioning (`POST /clinics`), hospital self-signup |
| `patients.py`, `patient_portal.py` | Staff Patient Registry vs. the authenticated patient-portal API (marketplace browse, booking, account/plan, reviews) |
| `payments.py` | Public gateway callbacks — SSLCommerz IPN + redirect bounce |
| `platform.py` | Platform-admin dashboard (revenue, billing, payment ledger) |
| `voice.py` | LiveKit token minting for browser voice calls |
| `conversations.py`, `messages.py` | Unified conversation threads, staff replies |
| `escalations.py` | Human-handoff queue, scoped to clinic/hospital/platform |
| `queue.py` | Walk-in/reception queue |
| `documents.py` | RAG source documents (upload, per-hospital) |
| `integrations.py` | Channel mapping (WhatsApp/SMS/SIP number → clinic) |
| `whatsapp.py`, `twilio_sms.py` | Inbound channel webhooks |
| `reports.py`, `audit.py` | Staff reporting, immutable audit log |
| `his.py` | Hospital-information-system webhook integration |

The route handlers are thin: chat/voice-token handlers call the runner or
mint a token; everything else calls `tools/database.py` (or `tools/rag.py`,
`tools/payments.py`).

### 2.3 The turn runner (`agent/runner.py`)

`run_turn(graph, session_id, message, **scope)` runs the graph for one turn
and returns `{reply, appointment_id, patient_name, done, ...}`.
`stream_turn(...)` yields the same content incrementally for the WebSocket
and voice worker, plus **custom stream events** for UI chrome that must never
be LLM-composed: `{type:"payment", ...}` (pay-now card with a deterministic
gateway URL) and `{type:"upgrade", ...}` (free-tier limit hit). These are
forwarded verbatim to the client — the LLM only ever produces the
accompanying prose, never the link.

The runner streams the graph in **`stream_mode="updates"`** and concatenates
`messages` from each node's state delta.

### 2.4 The LangGraph agent — a bound, tool-calling ReAct loop

This used to be an explicit multi-node finite-state machine (collect → offer
→ confirm → book, one node per phase). It has since been replaced by a
**single tool-calling loop** — a small, fixed graph where the LLM itself
decides which action to take by calling a tool, and Python only decides
*when the turn ends*:

```
START ──► call_model ──(tool_calls?)──► tools ──(booked?)──► post_booking ──► END
             ▲                            │
             └────────────(no)────────────┘
```

(`agent/graph.py`, wiring `agent/router.py::should_continue` /
`route_after_tools`.)

- **`call_model` node** (`nodes.py::call_model_node`) builds the system
  prompt (`build_system_prompt`), binds a tool schema sized to the session
  (see §2.6), calls the LLM, and returns its response (text and/or tool
  calls) as a new message.
- **`tools` node** is a stock LangGraph `ToolNode(ALL_TOOLS)` with a retry
  policy — it executes whatever the LLM called and appends `ToolMessage`s.
- **`post_booking` node** fires only when the just-executed tool result is a
  `BOOKED:` `ToolMessage` (keyed on the tool result content, not on
  `state["appointment_id"]`, so later list/cancel/reschedule/RAG calls in the
  same thread never re-trigger it). It emits a **deterministic** Bangla
  farewell (no LLM call — the outcome is already known) and persists
  [patient memory](#26-cross-session-patient-memory) — see
  `nodes.py::post_booking_node`.

`should_continue` routes back to `call_model` for another reasoning step
whenever the last message has `tool_calls`; otherwise the turn ends.

### 2.5 State (`agent/state.py`)

A `TypedDict` carried across the whole conversation via the LangGraph
checkpointer, keyed by `session_id` (the `thread_id`). Notable fields beyond
the obvious (`messages`, patient identity, `appointment_id`):

- `clinic_id` / `hospital_id` / `platform_mode` — tenant scope, set on turn 1
  and **never mutated** afterward (see §2.6 — this is a KV-cache invariant,
  not just a modeling choice).
- `patient_account_id` — set when the caller is an authenticated portal
  patient; unlocks the manage-tools (list/cancel/reschedule) and patient
  memory.
- `doctor_id`, `available_doctors`, `departments`, `offered_slots`,
  `my_appointments`, `last_visit` — scratch space tools write so the prompt
  and the UI (tappable slot/doctor pickers, a "book again" chip) can render
  structured data without re-deriving it from prose.
- `conversation_summary` — long threads are compacted by `call_model_node`:
  old turns are summarized into Bangla prose and dropped from `messages`,
  bounding CPU prefill time and `OLLAMA_NUM_CTX` usage.

### 2.6 Tools and tool-schema sizing (the KV-cache design)

`agent/tools.py` defines every LLM-callable action, grouped for binding:

- **`BOOKING_TOOLS`** — `select_department`, `list_doctors`, `choose_doctor`,
  `get_available_slots`, `book_appointment`. Always bound.
- **`MANAGE_TOOLS`** — `list_my_appointments`, `cancel_appointment`,
  `reschedule_appointment`. Bound only when `patient_account_id` is set —
  portal patients, and platform-number callers recognized by verified
  caller-ID (§8); anonymous callers on a hospital's dedicated number can't
  self-service someone else's booking.
- **RAG** — `search_hospital_info` (see [§2.7](#27-rag--tools-ragpy)). Bound
  when the hospital has documents, or always in platform mode.
- **`SEARCH_TOOLS`** — `search_doctors` (cross-hospital marketplace search).
  Bound only in `platform_mode`.
- **`request_human_help`** — escalation to a human; bound alongside booking.

The local model (`gemma4` by default, CPU) is small enough that **tool
schema size measurably affects latency and reliability**, and Ollama renders
the bound tool schema into the prompt — so a different binding is a
different prompt prefix, which invalidates Ollama's KV/prefix cache.
`nodes.py::_binding_flags(state)` is the **single source of truth** for
which tools a thread gets, computed once and reused identically for:

1. the real turn (`_llm_bound`),
2. prewarm (`_llm_prewarm` — same binding, `num_predict=1`, just pays the
   prefill early so the first real token isn't the slow one),
3. quick-reply chip suggestion (`_llm_suggest` — same binding, extends the
   just-finished turn's prompt so the prefix cache is reused).

Because of this, **tool binding must never change mid-thread**: platform-mode
threads bind `search_doctors` + `search_hospital_info` permanently from turn
1, even before `choose_doctor` lands the thread on a hospital with real
documents — flipping the binding later would churn the KV cache on every
such thread. This is also why `clinic_id`/`hospital_id`/`platform_mode` are
never mutated in state after the first turn (§2.5).

### 2.7 RAG (`tools/rag.py`)

`search_hospital_info` answers "what are your visiting hours / do you accept
this insurance" style questions from documents uploaded per hospital
(`api/routes/documents.py`). Two backends behind `RAG_BACKEND`:

- **`pgvector`** (default) — embeddings stored in Postgres, shared across
  worker processes.
- **`chroma`** — legacy local-file vector store, single-process only.

Embeddings use a local Ollama model (`EMBEDDING_MODEL=nomic-embed-text`), so
no document content leaves the box. In **platform mode** (marketplace home,
no hospital chosen yet), the search is **cross-hospital**: results are
prefixed with the hospital name (`[{hospital} — উৎস: {filename}]`) so the LLM
can attribute an answer to the right hospital instead of assuming the
patient's eventual choice.

### 2.8 Cross-session patient memory (`agent/memory.py`)

A second use of the LangGraph **store** (not the checkpointer) — one
namespace per patient account, `("patient_memory", str(account_id))`:

- Key `"profile"` — identity + `visit_count`.
- Key `"visit:{appointment_id}"` — one record per completed booking (doctor,
  department, hospital, slot, a Bangla `summary` line), written so
  `graph.py`'s store index can semantically recall it later ("গতবারের
  ডাক্তার" → last time's doctor).

Writes are **deterministic** (`post_booking_node` after a successful
booking) — never LLM-extracted, since the local model can't spare an
extraction call on top of everything else. Reads happen once per turn
(`nodes._load_patient_context`) and are rendered into the prompt **tail**,
not the KV-cache-stable head, so per-patient content never invalidates the
shared prefix.

### 2.9 LLM usage details (small-model-aware)

- **Every LLM call includes a human turn.** Some local models return *empty
  output* for a system-only prompt, so spoken/classification calls always
  send a `SystemMessage` **plus** a `HumanMessage`.
- **Prompt head/tail split** (`agent/prompts.py`): the system prompt has a
  KV-cache-stable **head** (mode instructions, RAG guidance, static rules)
  that is identical for the whole thread, and a **tail** (patient memory,
  conversation summary, corrective instructions) that varies per turn. Only
  the tail costs a fresh prefill.
- **Corrective retry, not canned strings.** When a tool call is malformed or
  the model's response doesn't match what the turn needs, the agent
  re-prompts the LLM with a corrective instruction (`_corrective_reply`)
  rather than falling back to a hardcoded Bangla string — every patient-facing
  sentence is LLM-composed.
- **Deterministic UI chrome is the one exception** — payment links and
  upgrade prompts are never put in front of the LLM (see §2.3); everything
  else the patient reads is generated.
- Prompts are instructions in English that require **Bangla** replies (the
  agent mirrors the patient's language in practice) and forbid
  markdown/emoji/placeholders.

### 2.10 Voice-safe text (`utils/text.py`)

`sanitize_text()` removes markdown, list prefixes, blockquotes, links, and
emojis. It is **Bangla-safe** — never ASCII-only. `normalize_bangla_digits()`
converts ০–৯ to 0–9 (and back, for spoken serial numbers). The runner applies
it to every reply; the voice path applies it before TTS.

### 2.11 Availability, booking, payments, billing

No calendar service — availability is **computed**:
`available_slots = (schedule slots) − (confirmed + unexpired pending-payment
appointments)`. `book_appointment` inserts either a `confirmed` row (fee = 0)
or a `pending_payment` hold (fee > 0, TTL `PAYMENT_TTL_MINUTES`); a **unique
partial index** on the slot makes double-booking impossible even across the
two statuses. `tools/database.py` also owns multi-tenant scoping, patient
plans (trial/premium/free), hospital billing state, reviews, and the
platform-admin revenue queries.

**Full monetization model** (fees, subscriptions, plan tiers, hospital
billing state machine, platform-admin dashboard, SSLCommerz setup): see
**[docs/MONETIZATION.md](docs/MONETIZATION.md)** — not duplicated here.

---

## 3. The Next.js app (`appointment-ui/`)

### 3.1 One codebase, two audiences, fully API-driven

All data access goes through `lib/api.ts` calling the FastAPI backend — there
is **no direct database access** from the frontend (an earlier version read
Postgres directly from Server Components; that path has been fully replaced).

- **Staff/admin console** — `app/{appointments,schedule,patients,queue,
  hospitals,integrations,conversations,messages,escalations,reports,
  audit,settings}` — clinic/hospital staff manage schedules, patients, the
  conversation/escalation queue, and channel integrations. Role-gated via
  `lib/auth.tsx` (staff JWT) and the role table in
  [§7.4](#74-roles-and-what-they-can-access).
- **Platform-admin dashboard** — `app/platform` (+ `app/platform-admin` login)
  — revenue, hospital billing, payment ledger. See
  [docs/MONETIZATION.md](docs/MONETIZATION.md).
- **Patient portal** (`app/portal/*`, `app/signup`) — a doctor-marketplace
  UX: home (hero, specialty tiles, hospital browse, search/filters), a
  doctor profile page (fees, rating summary, published reviews, next-slots
  preview), a booking sheet, an account page (plan/subscription/trial), and
  an appointments list. Authenticated via `lib/patientAuth.tsx` (separate
  patient JWT, distinct from staff auth).
- **Chat + voice UI** — `components/portal/{ChatPanel,FloatingAssistant,
  VoiceCall}.tsx` embed the same agent used everywhere else; `ChatPanel`
  renders the deterministic payment/upgrade events from §2.3 as cards, never
  as chat bubbles the LLM composed.

### 3.2 i18n (`lib/i18n.tsx`)

The **patient portal only** has a Bangla-first UI-chrome toggle (bn/en),
persisted per browser (`localStorage`). It covers static labels/buttons only
— the AI agent's own replies are LLM-composed and follow whatever language
the patient actually writes/speaks in, independent of this toggle. The staff
console is Bangla-only chrome (`lang="bn"` on the shell).

### 3.3 Files

| Area | Files | Role |
|---|---|---|
| Data | `lib/api.ts` | Every backend call the app makes (typed) |
| Auth | `lib/auth.tsx`, `lib/patientAuth.tsx` | Staff JWT vs. patient JWT session handling |
| i18n | `lib/i18n.tsx` | Portal bn/en toggle |
| Shell | `app/layout.tsx`, `components/layout/{Sidebar,TopBar}.tsx` | Staff console nav, Noto Sans Bengali |
| Portal shell | `app/portal/layout.tsx` | Patient portal nav/chrome |
| Portal marketplace | `components/portal/{HospitalBrowse,SpecialtyTiles,SearchFilters,DoctorCard}.tsx` | Browse/search doctors & hospitals |
| Portal booking | `components/portal/BookingSheet.tsx`, `app/portal/book/page.tsx` | Fee-aware booking flow (pay step when fee > 0) |
| Portal reviews | `components/portal/{RatingStars,ReviewModal}.tsx`, `components/settings/ReviewsManager.tsx` | Patient review submission + staff moderation |
| Chat/voice | `components/portal/{ChatPanel,FloatingAssistant,VoiceCall}.tsx` | Embedded agent, streaming + payment/upgrade cards |
| Schedule | `app/schedule/page.tsx`, `components/schedule/*` | Weekly editor + live slot preview |

---

## 4. The shared contract: the database + the REST API

**Alembic is the single source of truth for the schema** — `migrations/versions/`,
applied with `alembic upgrade head`. The backend container runs this
automatically on start (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

> `db/schema.sql` is a **legacy artifact from the original single-tenant MVP**
> (5 tables) and is far behind the current schema (dozens of tables across 28
> migrations — hospitals, clinics, channels, patient_accounts, payments,
> subscriptions, doctor_reviews, escalations, conversation_log, ...). **Do not
> use it to set up a database** — always run `alembic upgrade head`.

Because the Next.js app no longer reads Postgres directly (§3.1), the second
half of the shared contract is the **REST API** itself (`api/routes/*` +
`api/schemas.py`) — that's what actually keeps the two apps consistent now,
not raw table access.

---

## 5. Key design decisions (and trade-offs)

| Decision | Why | Trade-off |
|---|---|---|
| Tool-calling ReAct loop over an explicit per-phase FSM | Scales to many entry points (booking, search, RAG, manage, escalate) without one node per phase; the LLM picks the action, Python only decides when the turn ends | Correctness now depends on the model calling the right tool — mitigated by small, session-sized tool schemas + corrective retry |
| Tool schema **sized per session** and cached (`_binding_flags`) | Small local CPU model is latency- and reliability-sensitive to schema size; identical binding across turn/prewarm/suggest preserves Ollama's KV/prefix cache | Binding must be decided once at turn 1 and never mutated — more state-design discipline |
| `clinic_id`/`hospital_id`/`platform_mode` frozen after turn 1 | Any later change re-renders the prompt head and tool schema, paying a full cold prefill | `choose_doctor` in platform mode must resolve the hospital without touching these fields |
| Prompt **head/tail split** | Only the tail (memory, summary, corrective text) needs a fresh prefill each turn | Prompt assembly code must keep new per-turn content out of the head |
| Deterministic UI chrome (payment/upgrade) never through the LLM | A payment link can't be hallucinated, mis-stated, or omitted | The LLM's prose and the deterministic card can, in principle, disagree — mitigated with explicit `BOOKED_PENDING_PAYMENT`/`BOOKING_LIMIT_REACHED` tool-result strings the LLM must acknowledge truthfully |
| Corrective LLM retry over canned fallback strings | Every patient-facing sentence must be LLM-composed (no hardcoded copy to translate/maintain) | Slightly higher worst-case latency on a malformed turn |
| Postgres-only availability | No OAuth/calendar service; full control | No sync with a personal calendar |
| One graph invocation per turn + checkpointer | Natural fit for turn-based, multi-channel chat (web/WhatsApp/SMS/voice all drive the same graph) | Requires careful state-freezing discipline (see above) |
| UI is fully API-driven (no direct DB reads) | One contract (REST) for staff console, portal, and platform dashboard; enables the patient portal's separate auth/rate-limiting | An extra network hop vs. a Server Component querying Postgres directly |
| Local embeddings + pgvector for RAG | No document content leaves the box; shared across workers | `chroma` fallback exists but is single-process only |
| Patient memory writes are deterministic, not LLM-extracted | The CPU-bound model can't afford an extra extraction call per booking | Memory content is limited to what booking tools already know (no free-form facts) |

---

## 6. Where to change things

- **Add/remove a tool the agent can call** → `agent/tools.py` (define +
  add to `BOOKING_TOOLS`/`MANAGE_TOOLS`/`SEARCH_TOOLS`), then
  `nodes.py::_binding_flags`/`_tools_for` if it needs conditional binding.
- **Change what the agent says / its instructions** → `agent/prompts.py`
  (mind the head/tail split, §2.9).
- **Change availability, booking, or fee rules** → `tools/database.py`.
- **Change RAG behaviour** → `tools/rag.py`.
- **Change/add a payment provider** → `tools/payments.py`
  (`PaymentProvider` protocol) — see docs/MONETIZATION.md.
- **Add/modify a backend endpoint** → `api/routes/*` + `api/schemas.py`.
- **Swap the LLM** → `OLLAMA_MODEL` in `.env` (no code change); or
  `LLM_PROVIDER=gemini` to switch providers entirely.
- **Add a schema change** → a new Alembic revision in `migrations/versions/`
  (never edit `db/schema.sql`, §4).
- **Add a UI view** → `app/<route>/page.tsx` + calls added to `lib/api.ts`.

---

## 7. Multi-tenancy: hospitals, clinics, and channels

### 7.1 The two-layer model

The system has two layers. Understanding the distinction is critical before
changing anything that touches tenant resolution.

```
ADMIN LAYER (UI / RBAC)          AGENT LAYER (what runs per patient message)
──────────────────────────        ─────────────────────────────────────────────
hospitals                         channels
  └── clinics (departments)  ◄──────── kind + identifier ──► clinic_id
        └── users (staff)
        └── patients (MRNs)       agent resolves clinic_id (and, for the
        └── patient_accounts      marketplace/platform-mode entry point,
                                   hospital_id) → loads config, schedule,
                                   doctor info → books appointments
```

The hospital layer scopes staff accounts, patient medical records, billing,
and the admin UI's RBAC. **Per-clinic conversations still only need
`clinic_id`.** The one exception is the **platform-wide marketplace entry
point** (portal home / cross-hospital search, §2.6-2.7), where the agent
does resolve and carry `hospital_id`/`platform_mode` in state so it can scope
RAG and search across (or within) hospitals before a clinic is chosen.

### 7.2 Channel routing (how the agent finds its clinic)

Every clinic has one or more rows in the `channels` table:

```
channels
  kind        identifier              clinic_id
  ────────────────────────────────────────────
  whatsapp    +8801700000000           3
  sms         +8801700000000           3
  web         renegex-clinic           3
  sip         +8801700000000           3
```

When a message arrives — WhatsApp webhook, SMS webhook, web chat API call, or
SIP/browser voice call — `deps.resolve_channel_clinic(kind, identifier)`
looks up `clinic_id`. Everything from that point forward is scoped to that
clinic: greeting, doctor info, schedule, booking.

Unmapped **text** channels fall back to the default clinic (`slug =
"default"`) unless `STRICT_CHANNEL_ROUTING` rejects them — enable that for
real multi-tenant deployments. Unmapped **voice** calls are different: they
run in **platform mode** (cross-hospital search/RAG/booking) by default
(`VOICE_FALLBACK_SCOPE=platform`), because the platform's main phone number
is deliberately unmapped — see [§8 Voice](#8-voice) and
[docs/TELEPHONY.md](docs/TELEPHONY.md).

### 7.3 Provisioning a new tenant

Two paths:

- **`POST /clinics`** (requires `X-Platform-Key` = `PLATFORM_ADMIN_KEY`) —
  platform-admin tooling: creates a `hospitals` row, a linked `clinics` row,
  a default `web` channel, and the first `hospital_admin` user, atomically.
- **`POST /hospitals/signup`** (public, no key) — self-service signup used by
  the marketing site's "list your hospital" flow; seeds a free first month.
  See [docs/MONETIZATION.md](docs/MONETIZATION.md#hospital-billing).

After provisioning: the admin logs in and configures the schedule/doctors;
additional channels (WhatsApp number, SMS number, SIP number) are added via
the Integrations page, each mapping an identifier to `clinic_id`.

### 7.4 Roles and what they can access

| Role | Scope | Can do |
|---|---|---|
| `platform_admin` | All hospitals | Create tenants, view all hospitals, platform revenue/billing dashboard |
| `hospital_admin` | One hospital | Manage clinics/schedule/staff/appointments/patients, moderate reviews |
| `dept_head` | One department | Department-level management |
| `receptionist` | One hospital | Register patients, manage queue |
| `doctor` | One hospital | View their own schedule and appointments |

`platform_admin` has no `hospital_id` and no `clinic_id`. Pages that require
hospital scope show a hospital picker for platform admins.

### 7.5 Patient records: three related identities

- **`patients`** — the medical identity record (MRN, name, phone, age).
  Keyed on `hospital_id`. Created by staff via the admin UI Patient Registry.
- **`patient_accounts`** — the **portal login identity** (email/phone +
  password, JWT-authenticated). Owns plan/subscription state, reviews, and
  the cross-session memory namespace (§2.8). A portal booking links
  `patient_account_id` on the appointment.
- **`appointments`** — bookings, keyed on `clinic_id` + a phone number
  string, optionally linked to a `patient_account_id` when the caller was an
  authenticated portal patient. Anonymous/telephony callers (no account) are
  never rate-/plan-limited.

These three are still **not automatically reconciled** (e.g. a portal
patient account isn't auto-linked to a staff-created `patients` MRN row) —
that stitching remains a planned enhancement.

### 7.6 Database tables at a glance

```
hospitals              — top-level org (billing, admin layer)
clinics                — operational unit (used by both agent and admin)
  hospital_id  ──► hospitals.id
channels                — maps kind+identifier to clinic_id (agent entry point)
  clinic_id    ──► clinics.id
users                   — staff accounts
  hospital_id  ──► hospitals.id
  clinic_id    ──► clinics.id (department scope; nullable for platform_admin)
patients                — medical records, MRNs
  hospital_id  ──► hospitals.id
patient_accounts        — portal login identity, plan/subscription state
appointments            — bookings made by the agent or the portal
  clinic_id    ──► clinics.id
  account_id   ──► patient_accounts.id (nullable)
doctor_reviews           — 1-5 star patient reviews, one per (doctor, account)
payments                 — booking-fee + subscription payment ledger
hospital_subscriptions,
subscription_invoices    — hospital billing state
doctor_schedule          — weekly recurring hours
  clinic_id    ──► clinics.id
conversation_log,
escalations               — unified conversation threads + human handoff
audit_log                — immutable write log
  hospital_id  ──► hospitals.id
```

---

## 8. Voice

Voice is **fully implemented**, over two entry points, both driving the same
`agent/` core as chat:

- **Browser calls (patient portal)** — the patient presses a voice button;
  the browser joins a LiveKit room, the voice worker is dispatched into it.
  Live captions, mic-permission handling, and a post-call confirmation card
  in `components/portal/VoiceCall.tsx`. Full walkthrough:
  **[docs/VOICE_WEB.md](docs/VOICE_WEB.md)**.
- **SIP / phone telephony** — calls to the platform's main number run in
  **platform mode**: cross-hospital doctor search, RAG over any hospital's
  uploaded knowledgebase, booking anywhere — one number for the whole
  marketplace (`VOICE_FALLBACK_SCOPE=platform`, the default for unmapped
  calls). Calling it is a **premium/trial perk** (`VOICE_PREMIUM_GATE`):
  callers are matched by caller-ID against a one-time SMS-OTP-verified phone
  number, matched callers join their unified account thread (bookings linked,
  cancel/reschedule available), and everyone else gets an LLM-composed
  decline + SMS upgrade link. A hospital/clinic can still have a dedicated
  ungated scoped number via a `voice_ivr`/`voice_sip` channel mapping.
  Walkthrough: **[docs/TELEPHONY.md](docs/TELEPHONY.md)**.
- **Local mic (`python main.py console`)** — the original dev-loop entry
  point, still useful for quick manual testing without a browser or phone
  number.

STT/TTS engines are swappable via `.env` (`STT_ENGINE`, `TTS_ENGINE`):
local (`whisper` / `mms`), cloud (`gemini`), or `livekit` (LiveKit Inference,
zero extra provider keys, billed through LiveKit Cloud). See
`voice/stt_factory.py` / `voice/tts_factory.py` and
[docs/VOICE_WEB.md](docs/VOICE_WEB.md#switching-stttts-later).

---

## 9. Channels: WhatsApp, SMS, telephony

WhatsApp and SMS reuse the same agent core over a webhook instead of a
WebSocket, mapped to a clinic the same way as any other channel (§7.2). Full
setup (Meta app registration, Twilio vs. a local BD SMS gateway, mapping
numbers to clinics): **[docs/WHATSAPP_AND_SMS.md](docs/WHATSAPP_AND_SMS.md)**.

Telephony (inbound SIP/DID calls via LiveKit) setup:
**[docs/TELEPHONY.md](docs/TELEPHONY.md)**.

Scaling to multiple backend/worker replicas (Redis-backed rate limiting,
`OLLAMA_NUM_PARALLEL`, GPU): **[docs/SCALING.md](docs/SCALING.md)**.

# Architecture

This document explains how the codebase is structured and why. It is the
companion to `README.md` (which covers setup and commands). Read this to
understand *how the pieces fit together* before changing code.

---

## 1. System overview

Two applications share one PostgreSQL database:

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PostgreSQL (single DB)                         │
│   doctor_schedule │ appointments │ checkpoints (LangGraph state)       │
└───────▲───────────────────▲───────────────────────────────▲──────────┘
        │ asyncpg            │ asyncpg                        │ postgres.js
        │ (app data)         │ (checkpoints via psycopg)      │
┌───────┴────────────────────┴───────────┐          ┌────────┴──────────┐
│        FastAPI backend (api/)           │          │   Next.js UI       │
│  HTTP/WS chat → LangGraph → Ollama      │          │  (admin dashboard) │
└─────────────────────────────────────────┘          └───────────────────┘
```

- **The FastAPI backend** runs the conversational agent (text in → Bangla text
  out) and exposes REST for appointments and the doctor's schedule.
- **The Next.js UI** lets staff manage the schedule and review/cancel bookings.
- They never call each other. Their only contract is the **database schema**. A
  schedule change in the UI is visible to the next call automatically, because
  availability is computed from the DB at request time.

> A voice front-end (LiveKit + faster-whisper + Piper) is scaffolded under
> `voice/` and `main.py` but is **experimental / not yet runnable** — see
> [§7 Voice](#7-voice-experimental). The FastAPI backend is the supported runtime.

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
LangGraph graph             agent/graph.py + router.py        (control flow)
      │                     + nodes.py + prompts.py + state.py
      ▼
Tools                       tools/database.py                 (availability + data)
      ▼
PostgreSQL
```

| Layer | File(s) | Responsibility |
|---|---|---|
| Transport | `api/app.py`, `api/routes/*`, `api/schemas.py` | HTTP/WS endpoints, request/response models |
| Turn runner | `agent/runner.py` | Drive the graph for one turn; collect spoken text |
| Orchestration | `agent/graph.py`, `agent/router.py` | Graph wiring + deterministic control flow |
| Behaviour | `agent/nodes.py`, `agent/prompts.py` | What happens at each step |
| State | `agent/state.py` | The typed conversation state |
| Data | `tools/database.py` | Availability, booking, schedule, listing |
| Cross-cutting | `utils/text.py`, `config.py` | Sanitisation, settings |

### 2.2 The FastAPI layer (`api/`)

- **`api/app.py`** creates the `FastAPI` app with a **lifespan** that, on startup,
  builds the compiled graph once (`build_graph()`, which also creates the Postgres
  checkpoint tables) and warms the asyncpg pool; on shutdown it closes the pool.
  The compiled graph is stored on `app.state.graph` and reused for every request.
  CORS allows the Next.js UI origin.
- **`api/routes/chat.py`** — the agent endpoints:
  - `POST /chat/start` — opens a call, returns the Bangla greeting (the greeting
    node ignores message text, so it can be called with an empty message).
  - `POST /chat` — one patient message → the agent's reply for that turn.
  - `WS /chat/ws` — streaming: receive `{session_id, message}`, emit
    `{type:"token", text}` events then a final `{type:"end", …}`.
- **`api/routes/appointments.py`** — `GET /appointments` (filters),
  `PATCH /appointments/{id}` (cancel), `GET /availability`.
- **`api/routes/schedule.py`** — `GET /schedule`, `PUT /schedule`.
- **`api/schemas.py`** — Pydantic models for requests/responses.

The route handlers are thin: chat handlers call the runner; the others call
`tools/database.py`.

### 2.3 The turn runner (`agent/runner.py`)

`run_turn(graph, session_id, message)` runs the graph for one turn and returns
`{reply, phase, appointment_id, patient_name, done}`. `stream_turn(...)` yields the
same content incrementally for the WebSocket.

The runner streams the graph in **`stream_mode="updates"`** and concatenates the
`messages` from each node's state delta. This works because of a deliberate
invariant: **only *spoken* nodes add to `state["messages"]`.** Silent nodes
(extraction, slot-parse, yes/no-parse) return other keys and add no messages, so
they're naturally excluded from the reply — no tag filtering or token-level
streaming required. `session_id` is the LangGraph `thread_id`.

### 2.4 The LangGraph state machine

The conversation is an explicit **finite state machine**, not a free-form agent
loop. A phone booking has a fixed shape (collect info → offer times → confirm →
book), and a state machine makes it predictable, debuggable, and reliable on a
small local model.

**State** (`agent/state.py`) — a `TypedDict` carried across the whole call:

```
messages         (accumulated via the add_messages reducer)
phase            (drives per-turn routing)
patient_name / patient_age / patient_mobile
available_slots / selected_slot
confirmation     (transient yes/no parse result)
appointment_id   (set after a successful booking)
```

`messages` uses the `add_messages` reducer so each node returns only *new*
messages; history accumulates automatically. Every other field is a plain value a
node overwrites when it has something to set.

**Nodes** (`agent/nodes.py`) — async functions `state -> dict`, two kinds:

- *Spoken* nodes call the LLM to produce a Bangla reply and add it to `messages`
  (`greeting`, `collect_info` when info is missing, `present_slots`,
  `represent_slots`, `confirm_slot`, `booking_failed`, `farewell`).
- *Silent* nodes do work without speaking: `fetch_slots`/`book_appointment` hit
  the DB; `parse_selection`/`parse_confirmation` call the LLM but return structured
  fields, not messages.

**Routers** (`agent/router.py`) — pure functions `state -> next_node_name`. The
LLM never chooses the next node; Python does, based on `state`.

### 2.5 Graph wiring

```
                    ┌──────────── route_entry (conditional START edge) ───────────┐
                    │  phase=greeting → greeting                                   │
                    │  phase=collect_info → collect_info                           │
                    │  phase=present_slots → parse_selection                       │
                    │  phase=confirm_slot → parse_confirmation                     │
                    │  phase=farewell/done → farewell                              │
                    └─────────────────────────────────────────────────────────────┘

greeting ───────────────────────────────────────────────────────────────────► END
collect_info ──(missing info)────────────────────────────────────────────────► END
collect_info ──(all collected)──► fetch_slots ──► present_slots ──────────────► END
parse_selection ──(matched)────► confirm_slot ───────────────────────────────► END
parse_selection ──(unclear)────► represent_slots ────────────────────────────► END
parse_confirmation ──(yes)─────► book_appointment ──(ok)──► farewell ─────────► END
parse_confirmation ──(no)──────► fetch_slots ──► present_slots ───────────────► END
book_appointment ──(slot lost in race)──► booking_failed ──► fetch_slots ──► … ► END
```

`build_graph(checkpointer=None)` compiles this; it defaults to the shared
`AsyncPostgresSaver` but accepts any checkpointer (tests pass `InMemorySaver`).

### 2.6 The per-turn model (the key idea)

A normal LangGraph run goes START → … → END once; a call is many turns. We
reconcile these with **one graph invocation per turn** plus a **conditional entry
edge**:

1. Each turn the runner calls the graph with the new message and
   `config={"thread_id": session_id}`.
2. The checkpointer **loads prior state** for that thread.
3. `route_entry` reads `state["phase"]` and dispatches to the right node, so the
   user's message is interpreted in context (a selection vs a yes/no vs an info
   answer).
4. Node(s) run, possibly chaining forward (`collect_info → fetch_slots →
   present_slots`) until `END`.
5. The checkpointer **saves new state**; the turn ends.

`phase` is therefore "what the agent last asked".

### 2.7 LLM usage details (small-model-aware)

- **Every LLM call includes a human turn.** `llama3.2:3b` returns *empty output*
  for a system-only prompt, so `nodes._speak()`/`_classify()` always send a
  `SystemMessage` **plus** a `HumanMessage` (the patient's text, or a neutral
  Bangla nudge for agent-driven nodes like presenting slots).
- **`collect_info` makes two calls per turn.** A silent extraction call returns
  `{name, age, mobile}` as JSON (Bangla numerals normalised); a spoken call asks
  for the next missing field. Splitting the tasks keeps a 3B model reliable.
- **Phone numbers are captured deterministically.** Because the LLM can drop or
  transpose digits, `collect_info` also scans the message for a long digit run
  (after normalising Bangla numerals) and prefers that for the mobile number.
- Prompts are instructions in English that require **Bangla** replies and forbid
  markdown/emoji/placeholders.

### 2.8 Voice-safe text (`utils/text.py`)

`sanitize_text()` removes markdown, list prefixes, blockquotes, links, and emojis
(via the `emoji` library, which strips only emoji codepoints). It is **Bangla-safe**
— never ASCII-only — so Bengali script survives. `normalize_bangla_digits()`
converts ০–৯ to 0–9. The runner applies it to every reply; the (experimental)
voice path applies it before TTS.

### 2.9 Availability + booking (`tools/database.py`)

No calendar service. Availability is derived:

```
available_slots = (schedule slots for each upcoming day)  −  (confirmed appointments)
```

`get_available_slots()` reads `doctor_schedule`, generates slot datetimes per day
(skipping past times and days off), subtracts confirmed bookings, and returns slots
with Bangla spoken labels. `book_appointment()` inserts a confirmed row; a **unique
partial index** on `scheduled_at WHERE status='confirmed'` makes double-booking
impossible — a race returns `None`, which the graph turns into a `booking_failed`
→ re-offer flow. The module also provides `list_appointments`, `cancel_appointment`,
`get_schedule`, `save_schedule` for the REST API. asyncpg powers app queries; the
checkpointer uses psycopg over a separate pool; both point at the same database.

---

## 3. The admin UI (`appointment-ui/`)

### 3.1 Rendering model

Next.js App Router with a strict split:

- **Server Components** (pages, tables, dashboard widgets) query Postgres directly
  via `lib/queries.ts` — no client fetching, no spinners, no API round-trip for
  first paint.
- **Client Components** handle interaction only: `AppointmentFilters` (URL-param
  driven), `CancelDialog`, the `ScheduleEditor` form.
- **Mutations** go through **Server Actions** in `lib/actions.ts`
  (`cancelAppointment`, `saveSchedule`), which call `revalidatePath` to refresh.

There is deliberately **no `useEffect`+`fetch`**.

> The UI talks to Postgres directly today. It could instead call the FastAPI REST
> (`/appointments`, `/schedule`) if you want a single backend — the endpoints
> mirror what the UI does.

### 3.2 Files

| Area | Files | Role |
|---|---|---|
| Data | `lib/db.ts`, `lib/queries.ts`, `lib/actions.ts`, `types/index.ts` | DB client, reads, writes, types |
| Shell | `app/layout.tsx`, `components/layout/{Sidebar,TopBar}.tsx` | Nav, Noto Sans Bengali, `lang="bn"` |
| Dashboard | `app/page.tsx`, `components/dashboard/*` | Stats, today's timeline, upcoming list |
| Appointments | `app/appointments/page.tsx`, `components/appointments/*` | Filters, table, cancel dialog |
| Schedule | `app/schedule/page.tsx`, `components/schedule/*` | Weekly editor + live slot preview |

### 3.3 Schedule editing model

`doctor_schedule` stores only **active** days (a missing row = day off).
`getSchedule()` materialises all 7 days for the editor; `saveSchedule()` does a
transactional **delete-all + insert-active**, so the table reflects exactly what
the form shows.

---

## 4. The shared contract: the database

`db/schema.sql` is the single source of truth both apps depend on.

- `doctor_schedule` — recurring weekly hours (written by the UI, read by the agent)
- `appointments` — bookings (written by the agent, read/cancelled by the UI)
- `checkpoints*` — LangGraph state (created automatically by the checkpointer)

Because availability is computed from these tables at request time, the two apps
stay consistent without any direct integration.

---

## 5. Key design decisions (and trade-offs)

| Decision | Why | Trade-off |
|---|---|---|
| FastAPI backend (text) as the runtime | Runs now with installed deps; testable without audio | Voice needs separate work |
| State machine over a ReAct agent | Predictable, debuggable, reliable on a 3B model | Less flexible for off-script chatter |
| LLM only speaks/extracts; Python routes | Deterministic control flow | More routing code to maintain |
| Runner streams `updates`, not tokens | Robust; doesn't depend on model token streaming | Reply granularity is per-node, not per-token |
| Only spoken nodes add messages | Silent JSON never leaks into replies, no filtering | Convention nodes must follow |
| Human turn on every LLM call | `llama3.2:3b` returns nothing for system-only prompts | Tiny extra prompt plumbing |
| Deterministic phone-digit capture | LLMs drop/transpose digits | Only catches digit runs, not spelled-out numbers |
| Two LLM calls in `collect_info` | Small models fail at "extract + reply" together | Slightly more latency per turn |
| Postgres-only availability | No OAuth/calendar service; full control | No sync with a personal calendar |
| One invocation per turn + checkpointer | Natural fit for turn-based chat | Requires phase-based entry routing |
| UI reads DB directly | Simple, fast, no API layer for first paint | UI coupled to the schema |

---

## 6. Where to change things

- **Add a field to collect** → `state.py` (field), `prompts.py` (extract prompt),
  `nodes.py` (`collect_info_node`), `router.py` (`route_after_collect`).
- **Change what the agent says** → `agent/prompts.py`.
- **Change availability rules** → `tools/database.py:get_available_slots`.
- **Add/modify an endpoint** → `api/routes/*` + `api/schemas.py`.
- **Swap the LLM** → `OLLAMA_MODEL` in `.env` (no code change).
- **Add a UI view** → `app/<route>/page.tsx` (Server Component) + a query in
  `lib/queries.ts`; mutations as Server Actions in `lib/actions.ts`.

---

## 7. Multi-tenancy: hospitals, clinics, and channels

### 7.1 The two-layer model

The system has two layers that operate independently. Understanding the distinction is critical before changing any code that touches tenant resolution.

```
ADMIN LAYER (UI / RBAC)          AGENT LAYER (what runs per patient message)
──────────────────────────        ─────────────────────────────────────────────
hospitals                         channels
  └── clinics (departments)  ◄──────── kind + identifier ──► clinic_id
        └── users (staff)
        └── patients (MRNs)       agent resolves clinic_id → loads config,
                                  schedule, doctor info → books appointments
```

**The agent only ever knows about `clinic_id`. It has no concept of hospitals.**

The hospital layer is purely organisational — it scopes staff accounts, patient medical records, and the admin UI's role-based access control. It does not affect how the agent handles a patient conversation.

---

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

When a message arrives — WhatsApp webhook, SMS webhook, web chat API call, or SIP voice call — `deps.resolve_channel_clinic(kind, identifier)` looks up `clinic_id`. Everything from that point forward is scoped to that clinic: greeting message, doctor name, schedule, appointment booking.

A default clinic (`slug = "default"`) is the fallback when `strict_channel_routing` is disabled. In production it should be enabled.

---

### 7.3 Provisioning a new tenant (POST /clinics)

Calling `POST /clinics` with `X-Platform-Key` does the following atomically:

1. Creates a `hospitals` row — the administrative container
2. Creates a `clinics` row linked to that hospital — the operational unit the agent uses
3. Creates a default `web` channel mapped to the clinic slug
4. Creates the first `hospital_admin` user scoped to that hospital

After provisioning:
- The clinic admin logs in and configures the schedule and doctor details
- Additional channels (WhatsApp number, SMS number, SIP number) are added via the Integrations page — each maps a phone number / identifier to the `clinic_id`
- Once a channel is mapped, the agent will serve that channel

---

### 7.4 Roles and what they can access

| Role | Scope | Can do |
|---|---|---|
| `platform_admin` | All hospitals | Create tenants, view all hospitals, manage platform |
| `hospital_admin` | One hospital | Manage their clinic's schedule, staff, appointments, patients |
| `dept_head` | One department | Department-level management |
| `receptionist` | One hospital | Register patients, manage queue |
| `doctor` | One hospital | View their own schedule and appointments |

`platform_admin` has no `hospital_id` and no `clinic_id`. Pages that require hospital scope (Patients, Appointments, Queue etc.) show a hospital picker for platform admins.

---

### 7.5 Patient records vs. appointment records

These are different things stored in different tables:

- **`appointments`** — created by the agent when a patient books via chat/voice/SMS. Keyed on `clinic_id` and a phone number string. The agent writes these; the admin UI reads and cancels them.
- **`patients`** — the medical identity record (MRN, name, phone, age). Keyed on `hospital_id`. Created by staff via the admin UI Patient Registry. Not yet linked to appointments automatically.

The agent books by phone number; it does not look up or create `patients` rows. Joining these (auto-register a patient when the agent collects their details) is a planned enhancement.

---

### 7.6 Database tables at a glance

```
hospitals          — top-level org (admin layer only)
clinics            — operational unit (used by both agent and admin)
  hospital_id  ──► hospitals.id        (admin scope)
channels           — maps kind+identifier to clinic_id (agent entry point)
  clinic_id    ──► clinics.id
users              — staff accounts
  hospital_id  ──► hospitals.id
  clinic_id    ──► clinics.id (department scope; nullable for platform_admin)
patients           — medical records, MRNs
  hospital_id  ──► hospitals.id
appointments       — bookings made by the agent
  clinic_id    ──► clinics.id
doctor_schedule    — weekly recurring hours
  clinic_id    ──► clinics.id
audit_log          — immutable write log
  hospital_id  ──► hospitals.id
```

---

## 8. Voice (experimental)

`voice/assistant.py` and `main.py` scaffold a LiveKit voice agent (Silero VAD →
faster-whisper STT → the agent → Piper TTS). It is **not runnable yet** on
`livekit-agents 1.6.x`:

- The pipeline only invokes `Agent.llm_node`/`tts_node` when the `AgentSession` is
  given real `llm.LLM`/`tts.TTS` objects; passing `None` raises at runtime. Making
  voice work needs a `LangGraphLLM(llm.LLM)` wrapper and a `PiperTTS(tts.TTS)`
  wrapper (or the official `livekit-agents[langchain]` adapter for the LLM side).
- STT needs `faster-whisper` (the `taresh18/livekit-whisper` repo is not a pip
  package).

The agent core, runner, prompts, and DB tools are all reusable as-is once those
wrapper classes exist — only the transport changes.

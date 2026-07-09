# Doctor Appointment Setter Agent — Implementation Plan

## Context
Building a fully local, voice-first doctor appointment setter with two parts: (1) a Python voice agent using LangGraph + LiveKit Agents 1.6.x + Ollama + Piper TTS in Bangla, and (2) a Next.js 15 admin UI for clinic staff to manage the doctor's schedule and view/cancel appointments. Both share one PostgreSQL database. No cloud LLM, no Google Calendar, no OAuth.

---

## Tech Stack

| Layer | Package | Notes |
|---|---|---|
| Agent orchestration | `langgraph` 0.3.x | State machine with typed state |
| LLM | `langchain-ollama` (ChatOllama) | `llama3.2:3b` via local Ollama server |
| LG Checkpointing | `langgraph-checkpoint-postgres` 3.1.0 | AsyncPostgresSaver |
| DB driver | `asyncpg` | Async Postgres |
| Voice platform | `livekit-agents~=1.6` | Main voice SDK |
| VAD | `livekit-plugins-silero` | Local, no API key |
| STT | community `livekit-whisper` (faster-whisper) | Local, `language="bn"` for Bangla |
| TTS | **Piper TTS** (`piper-tts`) with `bn_BD-multi-medium` model | Local, open-source, Bangla-native |

> **LLM note:** `llama3.2:3b` has limited Bangla generation quality. If output quality is poor, switch to `qwen2.5:3b` (better multilingual) or `aya-expanse:8b` (purpose-built multilingual). The Ollama model is a single `.env` change.

---

## Repo Layout (two projects, one shared Postgres)

```
appointment-setter-agent/    <- Python voice agent (this repo)
appointment-ui/              <- Next.js admin UI (separate repo)
```

Both point at the same Postgres database. The agent writes `appointments`; the UI reads and manages both tables.

---

## Python Agent File Structure

```
appointment-setter-agent/
├── agent/
│   ├── __init__.py
│   ├── graph.py        # StateGraph definition + compile() with AsyncPostgresSaver
│   ├── state.py        # AppointmentState TypedDict
│   ├── nodes.py        # All 7 node functions
│   ├── router.py       # Conditional edge routing functions
│   └── prompts.py      # Per-node system prompts (plain text, no markdown)
├── tools/
│   ├── __init__.py
│   └── database.py     # asyncpg pool, get_available_slots(), book_appointment()
├── db/
│   └── schema.sql      # doctor_schedule + appointments tables + seed data
├── voice/
│   ├── __init__.py
│   └── assistant.py    # DoctorAssistant(Agent) with llm_node + tts_node override
├── utils/
│   ├── __init__.py
│   └── text.py         # sanitize_text() — strips markdown, emojis, bullets
├── models/             # Piper model files go here
│   ├── bn_BD-multi-medium.onnx
│   └── bn_BD-multi-medium.onnx.json
├── config.py           # Pydantic Settings reading from .env
├── main.py             # AgentServer entry point
├── requirements.txt
└── .env.example
```

---

## LangGraph State (`agent/state.py`)

```python
from typing import TypedDict, Annotated, Optional
from langgraph.graph import add_messages

class AppointmentState(TypedDict):
    messages: Annotated[list, add_messages]   # full conversation, auto-appended
    phase: str                                 # current node phase
    patient_name: Optional[str]
    patient_age: Optional[int]
    patient_mobile: Optional[str]
    available_slots: Optional[list[dict]]      # [{datetime, label}, ...]
    selected_slot: Optional[dict]              # patient's chosen slot
    appointment_id: Optional[str]             # UUID written after booking
```

Initial state: `{"messages": [], "phase": "greeting", "patient_name": None, ...}`

---

## State Machine: Nodes and Phases

| Node | Phase value | What it does |
|---|---|---|
| `greeting_node` | `"greeting"` | Greet patient, introduce purpose |
| `collect_info_node` | `"collect_info"` | Extract name/age/mobile; ask for one missing field at a time |
| `fetch_slots_node` | `"fetch_slots"` | Pure Python: query Postgres for available times (next 7 days) |
| `present_slots_node` | `"present_slots"` | Read out 3-5 available slots in plain speech |
| `confirm_slot_node` | `"confirm_slot"` | Read back chosen slot, ask patient to confirm yes/no |
| `book_appointment_node` | `"book_appointment"` | INSERT into appointments table, set appointment_id |
| `farewell_node` | `"farewell"` | Confirm booking details, say goodbye |

---

## Routing Logic (`agent/router.py`)

```
START -> greeting_node
greeting_node -> collect_info_node  (always)

collect_info_node:
  missing name, age, or mobile? -> collect_info_node  (loop back)
  all 3 collected?              -> fetch_slots_node

fetch_slots_node -> present_slots_node  (always)

present_slots_node:
  no valid slot selected? -> present_slots_node  (re-prompt)
  slot selected?          -> confirm_slot_node

confirm_slot_node:
  patient said no/change?  -> present_slots_node
  patient confirmed yes?   -> book_appointment_node

book_appointment_node -> farewell_node -> END
```

Each router function receives `state: AppointmentState` and returns a node name string. Routing is deterministic Python — the LLM never decides which node to go to.

---

## Info Extraction Strategy (`collect_info_node`)

Model is `llama3.2:3b` — a smaller model, so prompts must be short, explicit, and low-ambiguity. We use **two separate LLM calls per turn** to keep each task simple:

**Call 1 — Extract only (no conversation):**
```
Prompt: "From this message: '{user_msg}', extract any of these if present:
name (string), age (integer), mobile (digits only as string).
Bangla numerals like 0123456789 should be converted to digits.
Output ONLY valid JSON on one line. Example: {"name":"rahela","age":35,"mobile":"01711000000"}"
```
Parse the JSON; update state fields.

**Call 2 — Respond only (no extraction):**
```
Prompt: "You are a doctor's appointment setter assistant. Always respond in Bangla.
Already collected: {collected}. Still need: {missing}.
Ask for the next missing field in one short, polite Bangla sentence."
```
This response goes to Piper TTS. All node prompts include the instruction: **"Always respond in Bangla."**

**Bangla number handling:** Patient may say mobile numbers in Bangla digits or words. The extraction prompt instructs the LLM to normalize these to ASCII digits before putting them in JSON.

---

## LiveKit <-> LangGraph Wiring (`voice/assistant.py`)

LiveKit's `Agent` class has `llm_node` and `tts_node` methods that can be fully overridden. This is the integration point:

```
Patient speaks
    -> Silero VAD detects end of turn
    -> faster-whisper STT transcribes (language="bn")
    -> llm_node called with chat_ctx
        -> extract last user message
        -> graph.astream({"messages": [HumanMessage(content=...)]}, config=thread_config)
        -> for each token chunk: sanitize_text() -> yield ChatChunk
    -> tts_node called with token stream
        -> accumulate full text
        -> Piper synthesizes Bangla audio
        -> yield rtc.AudioFrame
    -> Audio plays to patient
```

```python
class DoctorAssistant(Agent):
    def __init__(self, graph, session_id: str, piper_voice):
        super().__init__(instructions="You are a doctor's appointment setter.")
        self._graph = graph
        self._piper = piper_voice
        self._config = {"configurable": {"thread_id": session_id}}

    async def llm_node(self, chat_ctx, tools, model_settings):
        last_user_msg = next(
            (m.content for m in reversed(chat_ctx.messages) if m.role == "user"), ""
        )
        async for mode, chunk in self._graph.astream(
            {"messages": [HumanMessage(content=last_user_msg)]},
            config=self._config,
            stream_mode=["messages"],
        ):
            if mode == "messages":
                msg_chunk, _ = chunk
                if hasattr(msg_chunk, "content") and msg_chunk.content:
                    clean = sanitize_text(msg_chunk.content)
                    if clean:
                        yield llm.ChatChunk(
                            id="chunk",
                            delta=llm.ChoiceDelta(role="assistant", content=clean)
                        )

    async def tts_node(self, text, model_settings):
        full_text = ""
        async for chunk in text:
            full_text += chunk
        audio_bytes = b"".join(self._piper.synthesize_stream_raw(full_text))
        yield rtc.AudioFrame(
            data=audio_bytes,
            sample_rate=22050,
            num_channels=1,
            samples_per_channel=len(audio_bytes) // 2,
        )
```

The `session_id` maps to LiveKit's room name — this becomes the LangGraph `thread_id`, tying all turns of one call together via the Postgres checkpointer.

---

## Database Schema (`db/schema.sql`)

```sql
-- Doctor's weekly schedule (managed by admin / Next.js UI)
CREATE TABLE doctor_schedule (
    id           SERIAL PRIMARY KEY,
    day_of_week  SMALLINT NOT NULL,        -- 0=Mon, 1=Tue, ..., 6=Sun
    start_time   TIME NOT NULL,            -- e.g. '09:00'
    end_time     TIME NOT NULL,            -- e.g. '17:00'
    slot_duration INTEGER NOT NULL DEFAULT 30  -- minutes per slot
);

-- Booked appointments (written by agent)
CREATE TABLE appointments (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_name   TEXT NOT NULL,
    patient_age    INTEGER NOT NULL,
    patient_mobile TEXT NOT NULL,
    scheduled_at   TIMESTAMPTZ NOT NULL,
    duration_mins  INTEGER NOT NULL DEFAULT 30,
    status         TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | cancelled
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- Seed: Mon-Fri, 9am-5pm, 30-min slots
INSERT INTO doctor_schedule (day_of_week, start_time, end_time, slot_duration)
VALUES (0,'09:00','17:00',30),(1,'09:00','17:00',30),(2,'09:00','17:00',30),
       (3,'09:00','17:00',30),(4,'09:00','17:00',30);
```

`get_available_slots(days_ahead=7)` logic in `tools/database.py`:
1. Fetch `doctor_schedule` rows for weekdays in the next `days_ahead` days
2. Generate all slot datetimes for each day
3. `SELECT scheduled_at FROM appointments WHERE status='confirmed'` in that range
4. Return slots not already booked as `[{"datetime": ..., "label": "Monday June 23rd at 9 AM"}]`

---

## Text Sanitizer (`utils/text.py`)

Applied to every token chunk before it reaches TTS. Must be **Bangla-safe** — do not strip Unicode characters, as Bangla script is non-ASCII:
- Strip markdown: `**`, `*`, `_`, backticks, `#`, links `[text](url)`
- Strip bullet points and numbered list prefixes
- Strip emojis using the `emoji` library (`emoji.replace_emoji(text, replace="")`) — removes only emoji codepoints, preserves Bangla Unicode
- Collapse multiple spaces/newlines to a single space

---

## STT: Whisper (local, Bangla)

Community `livekit-whisper` plugin using faster-whisper in-process. Whisper natively supports Bangla (`bn`).

```bash
pip install git+https://github.com/taresh18/livekit-whisper
```

```python
from whisper_plugin import WhisperSTT

stt = WhisperSTT(
    model="base",       # or "large-v3-turbo" for better Bangla accuracy
    language="bn",      # locks to Bengali, improves speed and accuracy
    device="cpu",       # or "cuda"
)
```

No extra server required — runs in-process.

---

## TTS: Piper TTS (local, Bangla)

Kokoro is English-only. **Piper TTS** is the replacement — fully local, MIT-licensed, has a `bn_BD-multi-medium` Bengali model.

```bash
pip install piper-tts
```

Download model files once at setup:
```bash
mkdir -p models
# Download bn_BD-multi-medium.onnx and bn_BD-multi-medium.onnx.json from
# https://huggingface.co/rhasspy/piper-voices/tree/main/bn/bn_BD/multi/medium
```

`PiperVoice` is loaded once at startup:
```python
from piper import PiperVoice
piper_voice = PiperVoice.load("models/bn_BD-multi-medium.onnx")
```

---

## `main.py` Entry Point

```python
server = AgentServer()
piper_voice = PiperVoice.load(settings.piper_model_path)  # loaded once at startup

@server.rtc_session(agent_name="appointment-setter")
async def session_handler(ctx: agents.JobContext):
    graph = await build_graph()
    session_id = ctx.room.name

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=WhisperSTT(model=settings.whisper_model, language="bn"),
        tts=None,   # overridden by tts_node
        llm=None,   # overridden by llm_node
    )
    await session.start(room=ctx.room, agent=DoctorAssistant(graph, session_id, piper_voice))
    await session.generate_reply(instructions="Greet the patient in Bangla and begin.")

if __name__ == "__main__":
    agents.cli.run_app(server)
```

---

## `.env.example`

```
# Postgres
DATABASE_URL=postgresql://user:password@localhost:5432/appointments

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b

# LiveKit
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=devsecret

# Piper TTS
PIPER_MODEL_PATH=models/bn_BD-multi-medium.onnx

# STT
WHISPER_MODEL=base
STT_LANGUAGE=bn
```

---

## Next.js UI (`appointment-ui/`)

### Purpose
Admin dashboard for clinic staff. Manage the doctor's weekly schedule, view all appointments, cancel bookings, and get a day-at-a-glance view of the appointment load. Reads/writes the same Postgres DB the agent uses — no separate API layer needed.

---

### Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Framework | Next.js 16 (App Router, TypeScript) | Server Components for data, Client Components for interaction (15 planned; npm pulled patched 16) |
| Styling | Tailwind CSS | Utility-first |
| Components | shadcn/ui | Pre-built accessible components (Table, Dialog, Badge, Switch, Select) |
| DB client | `postgres` npm package | Direct Postgres in Server Components and Server Actions |
| Date handling | `date-fns` | Format, parse, range utilities |
| Bangla font | Noto Sans Bengali via `next/font/google` | Renders Bengali script correctly |
| Icons | `lucide-react` | Included with shadcn/ui |

---

### File Structure

```
appointment-ui/
├── app/
│   ├── layout.tsx                        # Root layout: font, sidebar, lang="bn"
│   ├── page.tsx                          # Dashboard (Server Component)
│   ├── appointments/
│   │   └── page.tsx                      # Appointment list (Server Component + client filters)
│   ├── schedule/
│   │   └── page.tsx                      # Schedule editor (Client Component)
│   └── api/
│       ├── appointments/
│       │   ├── route.ts                  # GET /api/appointments
│       │   └── [id]/route.ts             # PATCH /api/appointments/[id]
│       └── schedule/
│           └── route.ts                  # GET + PUT /api/schedule
│
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx                   # Fixed left nav with icons + labels
│   │   └── TopBar.tsx                    # Page title + breadcrumb
│   ├── dashboard/
│   │   ├── StatsRow.tsx                  # 4 stat cards across the top
│   │   ├── TodayTimeline.tsx             # Visual hourly slot grid for today
│   │   └── UpcomingTable.tsx             # Next 5 appointments with quick cancel
│   ├── appointments/
│   │   ├── AppointmentFilters.tsx        # Date picker + status filter + search (client)
│   │   ├── AppointmentTable.tsx          # Full sortable table (server-rendered rows)
│   │   └── CancelDialog.tsx             # Confirmation modal before cancelling
│   ├── schedule/
│   │   ├── ScheduleEditor.tsx            # Full weekly schedule form
│   │   ├── DayRow.tsx                    # Single day: toggle + times + duration
│   │   └── SlotPreview.tsx               # Shows slot count preview as settings change
│   └── ui/
│       └── StatusBadge.tsx               # confirmed=green | cancelled=red pill
│
├── lib/
│   ├── db.ts                             # postgres client singleton
│   ├── queries.ts                        # all SQL query functions
│   └── actions.ts                        # Server Actions (cancel, save schedule)
│
├── types/
│   └── index.ts                          # Appointment, ScheduleRow, StatsData types
│
├── tailwind.config.ts
├── next.config.ts
├── .env.local
└── package.json
```

---

### Overall Layout

Fixed two-column layout: narrow sidebar on the left, scrollable main content on the right.

```
+------------------+----------------------------------------------+
|                  |  TopBar: "Dashboard"              [date]     |
|   LOGO           +----------------------------------------------+
|                  |                                              |
|   Dashboard      |   [main page content]                        |
|   Appointments   |                                              |
|   Schedule       |                                              |
|                  |                                              |
|                  |                                              |
+------------------+----------------------------------------------+
```

`Sidebar.tsx` — fixed, `w-56`, white background, border-right. Nav items use `lucide-react` icons (LayoutDashboard, CalendarDays, Clock). Active item highlighted with a left border accent and bg-slate-100.

`app/layout.tsx` structure:
```tsx
<html lang="bn">
  <body className={notoSansBengali.className}>
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto p-6 bg-slate-50">
          {children}
        </main>
      </div>
    </div>
  </body>
</html>
```

---

### Page 1: Dashboard (`/`)

**Purpose:** At-a-glance view of today's workload and upcoming bookings.

**Layout:**
```
[StatsRow: 4 cards]
 Today's Appts | This Week | Available Today | Cancellations

[TodayTimeline]
 09:00  [BOOKED — রাহেলা বেগম]
 09:30  [available]
 10:00  [BOOKED — করিম সাহেব]
 ...

[UpcomingTable: next 5 appointments]
 Name | Age | Mobile | Time | Status | Cancel
```

**`StatsRow.tsx`** — 4 `StatsCard` components side by side:
- Today's confirmed count
- This week's confirmed count
- Available slots remaining today
- Cancellations this week

Each card: large number, small label, subtle icon, light background color.

**`TodayTimeline.tsx`** — Server Component. Fetches today's schedule slots and booked appointments. Renders each 30-min block as a row:
- Booked: blue background, patient name in Bangla, age
- Available: white/gray background, "খালি" (available) label

**`UpcomingTable.tsx`** — Server Component. Shows next 5 confirmed appointments across all dates. Each row has a Cancel button that opens `CancelDialog`.

**Data fetching:** This whole page is a Server Component. It runs SQL at request time — no loading spinners, no client fetch.

---

### Page 2: Appointments (`/appointments`)

**Purpose:** Full browsable history of all appointments with filtering and cancellation.

**Layout:**
```
[AppointmentFilters: date range | status dropdown | name search]

[AppointmentTable]
 Name | Age | Mobile | Scheduled At | Duration | Status | Booked At | Actions
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 রাহেলা বেগম | 42 | 01711... | Mon Jun 23, 9:00 AM | 30 min | confirmed | ... | [Cancel]
 করিম সাহেব  | 55 | 01811... | Mon Jun 23, 10:00 AM | 30 min | cancelled | ... | —

[Pagination]
```

**`AppointmentFilters.tsx`** — Client Component. Controls live URL search params:
- Date range: two `<input type="date">` fields (from / to)
- Status: shadcn `Select` — All / Confirmed / Cancelled
- Search: text input matching patient name or mobile
- Changing any filter pushes to `router.push` with updated `?date_from=&date_to=&status=&q=` params

**`AppointmentTable.tsx`** — Server Component. Reads URL search params, runs parameterised SQL query, renders rows. No client JS needed for the table itself.

**`CancelDialog.tsx`** — Client Component (`"use client"`). shadcn `AlertDialog`. Triggered by Cancel button. Calls Server Action `cancelAppointment(id)` on confirm. Updates UI via `router.refresh()` after success.

**Columns:** Name (Bangla-safe), Age, Mobile, Scheduled At (formatted with `date-fns`), Duration, `StatusBadge`, Booked At, Actions.

---

### Page 3: Schedule (`/schedule`)

**Purpose:** Set the doctor's working hours per day of week. Changes take effect on the next voice call.

**Layout:**
```
Weekly Schedule
Save Changes  [button]

Day       Active   Start    End      Slot Duration   Preview
Monday    [on]     09:00    17:00    30 min          16 slots/day
Tuesday   [on]     09:00    17:00    30 min          16 slots/day
Wednesday [on]     09:00    13:00    30 min          8 slots/day
Thursday  [on]     09:00    17:00    30 min          16 slots/day
Friday    [on]     09:00    17:00    30 min          16 slots/day
Saturday  [off]    —        —        —               —
Sunday    [off]    —        —        —               —
```

**`ScheduleEditor.tsx`** — Client Component. Holds form state for all 7 days. On mount, fetches current schedule via `GET /api/schedule`. On save, calls `PUT /api/schedule`.

**`DayRow.tsx`** — single row per day. Props: `day`, `active`, `startTime`, `endTime`, `slotDuration`, `onChange`. Uses shadcn `Switch` for active toggle, `Input` for times, `Select` for slot duration (15 / 30 / 45 / 60 min).

**`SlotPreview.tsx`** — purely computed: `Math.floor((endTime - startTime) / slotDuration)`. Updates live as user changes inputs. Shows "X slots/day".

**Save flow:** `PUT /api/schedule` sends the full 7-row array. API route runs `DELETE FROM doctor_schedule` + batch `INSERT` in one transaction. Shows a shadcn `Toast` on success or error.

---

### Component Responsibilities Summary

| Component | Type | Responsibility |
|---|---|---|
| `Sidebar` | Client | Navigation, active link highlighting |
| `TopBar` | Server | Page title, current date display |
| `StatsRow` | Server | 4 metric cards from DB aggregates |
| `TodayTimeline` | Server | Slot-by-slot view of today |
| `UpcomingTable` | Server | Next 5 appointments |
| `AppointmentFilters` | Client | URL search param controls |
| `AppointmentTable` | Server | Filtered, paginated appointment rows |
| `CancelDialog` | Client | Confirm + call Server Action to cancel |
| `StatusBadge` | Server/Client | Confirmed=green / Cancelled=red pill |
| `ScheduleEditor` | Client | Full form state for weekly schedule |
| `DayRow` | Client | Single day controls within the editor |
| `SlotPreview` | Client | Live computed slot count |

---

### Data Fetching Strategy

| Page | Approach | Why |
|---|---|---|
| Dashboard | Server Component, direct SQL | Static at request time, no interactivity needed |
| Appointments (table) | Server Component, reads URL params | Filters are URL-driven, SSR is fine |
| Appointments (filters) | Client Component | Live interaction with URL params |
| Schedule | Client Component | Form state must live in the browser |

**No `useEffect` + `fetch` patterns.** Server Components query the DB directly via `lib/queries.ts`. Client Components that need to mutate call Server Actions in `lib/actions.ts`.

---

### `lib/queries.ts` — Key Functions

```typescript
getTodayAppointments(): Promise<Appointment[]>
getUpcomingAppointments(limit: number): Promise<Appointment[]>
getAppointments(filters: AppointmentFilters): Promise<Appointment[]>
getDashboardStats(): Promise<StatsData>
getSchedule(): Promise<ScheduleRow[]>
```

### `lib/actions.ts` — Server Actions

```typescript
"use server"
cancelAppointment(id: string): Promise<void>   // UPDATE status='cancelled'
saveSchedule(rows: ScheduleRow[]): Promise<void> // DELETE + INSERT in transaction
```

---

### API Routes

```
GET   /api/appointments            list with ?date_from= ?date_to= ?status= ?q=
PATCH /api/appointments/[id]       { status: "cancelled" }
GET   /api/schedule                returns all 7 rows ordered by day_of_week
PUT   /api/schedule                replaces all rows atomically
```

API routes are used by external consumers (e.g. the voice agent could query them, or a mobile app later). Internal UI mutations use Server Actions instead.

---

### `types/index.ts`

```typescript
export type Appointment = {
  id: string
  patient_name: string
  patient_age: number
  patient_mobile: string
  scheduled_at: Date
  duration_mins: number
  status: "confirmed" | "cancelled"
  created_at: Date
}

export type ScheduleRow = {
  id?: number
  day_of_week: number       // 0=Mon ... 6=Sun
  start_time: string        // "09:00"
  end_time: string          // "17:00"
  slot_duration: number     // minutes
  active: boolean           // UI-only field, not in DB (absent row = inactive)
}

export type StatsData = {
  today_count: number
  week_count: number
  available_today: number
  cancellations_week: number
}
```

---

### Bangla Support in UI
- `app/layout.tsx`: `<html lang="bn">` + `next/font/google` Noto Sans Bengali loaded via `font-display: swap`
- Patient names, ages, and mobile numbers stored as UTF-8 in Postgres and render correctly without any extra configuration
- UI chrome (nav labels, buttons, column headers) stays in English — this is a staff-facing tool, not patient-facing
- The `TodayTimeline` can show "খালি" (empty/available) for unbooked slots as a small Bangla affordance

---

### `.env.local`

```
DATABASE_URL=postgresql://user:password@localhost:5432/appointments
```

---

## Implementation Order

### Phase 1 — Shared DB
1. Write and run `db/schema.sql` against Postgres

### Phase 2 — Python Agent
2. `config.py` — Pydantic Settings
3. `tools/database.py` — asyncpg pool, `get_available_slots()`, `book_appointment()`
4. `agent/state.py` — TypedDict
5. `agent/prompts.py` — system prompts (English instructions, Bangla output)
6. `agent/nodes.py` — 7 node functions using ChatOllama
7. `agent/router.py` — routing functions
8. `agent/graph.py` — `build_graph()` with AsyncPostgresSaver
9. `utils/text.py` — `sanitize_text()` (emoji-safe for Bangla Unicode)
10. `voice/assistant.py` — `DoctorAssistant` with `llm_node` + `tts_node` overrides
11. `main.py` — AgentServer + session handler
12. `requirements.txt` + `.env.example`

### Phase 3 — Next.js UI
13. Scaffold: `npx create-next-app@latest appointment-ui --typescript --tailwind --app`
14. Install deps: `npm install postgres date-fns lucide-react` + `npx shadcn@latest init`
15. `types/index.ts` — Appointment, ScheduleRow, StatsData types
16. `lib/db.ts` — postgres client singleton
17. `lib/queries.ts` — all SQL query functions
18. `lib/actions.ts` — Server Actions: cancelAppointment, saveSchedule
19. `app/api/appointments/route.ts` + `[id]/route.ts` — GET + PATCH
20. `app/api/schedule/route.ts` — GET + PUT
21. `components/ui/StatusBadge.tsx`
22. `components/layout/Sidebar.tsx` + `TopBar.tsx`
23. `app/layout.tsx` — Noto Sans Bengali font, sidebar, `lang="bn"`
24. `components/dashboard/StatsRow.tsx` + `TodayTimeline.tsx` + `UpcomingTable.tsx`
25. `app/page.tsx` — Dashboard (Server Component)
26. `components/appointments/AppointmentFilters.tsx` + `CancelDialog.tsx`
27. `components/appointments/AppointmentTable.tsx`
28. `app/appointments/page.tsx` — Appointment list
29. `components/schedule/DayRow.tsx` + `SlotPreview.tsx` + `ScheduleEditor.tsx`
30. `app/schedule/page.tsx` — Schedule editor

---

## Verification

### Agent
1. Start Postgres, run `ollama serve`, load Piper model, start LiveKit server
2. Run `python main.py console` (text I/O, no mic needed)
3. Walk a full call:
   - Type a Bangla message to start
   - Agent asks for name, age, mobile (in Bangla)
   - Agent presents available slots
   - Pick a slot and confirm
   - Agent confirms booking and says goodbye
4. `SELECT * FROM appointments;` — verify record was written
5. Start a new session — verify the booked slot no longer appears as available
6. `python main.py dev` — real voice test with LiveKit room and Bangla speech

### Next.js UI
1. `npm run dev` in `appointment-ui/`
2. Open `http://localhost:3000` — dashboard shows today's appointment
3. Go to `/appointments` — cancel the test appointment
4. `SELECT status FROM appointments;` — verify `cancelled`
5. Go to `/schedule` — change slot duration to 60 min, save
6. Run another voice call — verify agent only offers 60-min slots

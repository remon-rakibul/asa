# Plan: Patient Portal + Self-Signup + Agent Long-Term Memory (+ future RAG & MCP)

> Status: planning doc. Phases 1–2 are the immediate build; Phases 3–4 are documented for later.
> Grounded in the LangChain docs (docs.langchain.com MCP + doc pages cited inline).

## Context

The system today is **admin-only**. The hierarchy `hospitals → clinics (= departments, with
`hospital_id`/`specialty_code`/`floor`) → doctors → appointments` exists, and a ReAct LangGraph agent
(`agent/graph.py`) books appointments over web chat / WhatsApp / SMS / voice (SIP). Gaps:

- **Patients have no login.** The `patients` table (hospital-scoped: `hospital_id`, MRN, phone) is
  populated only by staff. `appointment-ui/app/chat/page.tsx` is an anonymous demo with no patient
  identity.
- **The agent has no long-term memory.** `agent/graph.py` wires an `AsyncPostgresSaver` checkpointer
  (per-thread message history, line ~49) but **no `PostgresStore`**, so a returning patient
  re-answers name/age every session.
- Staff roles only (`users.role` CHECK): `platform_admin, hospital_admin, dept_head, receptionist,
  doctor` — no patient role.

**Goal:** a patient self-registers on the site, logs in, picks hospital → department → doctor, and
books via chat *and* voice; the agent remembers them across sessions. Hospital admins keep managing
departments/doctors/patients; the platform admin keeps managing hospitals and monitoring. Then (future
phases) add **RAG over per-hospital documents with pgvector** and **expose the agent's tools as an MCP
server**.

### Confirmed product decisions
1. **Platform-wide patient login** — one account; a hospital-scoped `patients` (MRN) row is
   auto-created on first booking at a given hospital.
2. **Separate `patient_accounts` table** — keep patient credentials out of staff `users`; do not widen
   the RBAC role enum.
3. **Memory remembers:** profile (name/age/phone) + visit history + preferences.

### Docs grounding (verified via docs.langchain.com MCP server, 2026-06-24)
- Long-term memory → `PostgresStore`: `/oss/python/langgraph/add-memory` ("Use in production") and
  `/oss/python/langgraph/stores` (namespace/key, `store.aput`/`aget`/`search`, `list_namespaces`).
- HITL (optional doctor approval) → `interrupt()`: `/oss/python/langgraph/interrupts`
  (resume via `Command(resume=...)`, needs a checkpointer — already present).
- RAG → `langchain_postgres.PGVector` (`/oss/python/integrations/vectorstores/index#pgvector`):
  `PGVector(embeddings=..., collection_name=..., connection="postgresql+psycopg://...",
  use_jsonb=True)`, `add_documents(docs)`, `similarity_search(query, filter={...})`,
  `.as_retriever(search_kwargs={"filter": {"hospital_id": {"$eq": id}}})`. **psycopg3 only** —
  connection string must use `postgresql+psycopg://` (not `postgresql+asyncpg://`).
  Tenant isolation via `metadata={"hospital_id": id}` on `add_documents` + `$eq` filter on search.
- MCP adapters → `langchain_mcp_adapters.client.MultiServerMCPClient` (`/oss/python/langchain/mcp`):
  `pip install langchain-mcp-adapters`. Transports: `stdio` (subprocess, best for local) or `http`
  (streamable HTTP, best for remote). **Stateless by default** — each tool invocation creates a fresh
  `ClientSession`. Use as context manager for the lifetime of the app:
  ```python
  async with MultiServerMCPClient({"booking": {"transport": "stdio", "command": "python",
      "args": ["/abs/path/to/mcp_server.py"]}}) as client:
      tools = await client.get_tools()
  ```
  or non-context-manager: `client = MultiServerMCPClient({...})` + `await client.get_tools()`.

---

## Phase 1 — Patient accounts, self-signup, patient-facing booking

### 1a. Schema — `migrations/versions/0015_patient_accounts.py`
```sql
CREATE TABLE patient_accounts (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    phone         TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_patient_accounts_phone ON patient_accounts (phone);

ALTER TABLE patients ADD COLUMN account_id INTEGER
    REFERENCES patient_accounts(id) ON DELETE SET NULL;
CREATE INDEX ix_patients_account ON patients (account_id);
```
Password via `tools.auth.hash_password`; patient PII follows the existing `encrypt_field` policy when
`PATIENT_ENCRYPTION_KEY` is set.

### 1b. Data layer — `tools/database.py` (mirror `create_user`/`get_user_by_email`)
- `create_patient_account(*, email, password_hash, name, phone)` → `Optional[dict]` (UniqueViolation→None).
- `get_patient_account_by_email(email)`, `get_patient_account(account_id)`.
- Extend `get_or_create_patient(...)` (≈line 939) to accept/set `account_id`; add
  `link_patient_to_account(patient_id, account_id)`.
- `list_appointments_for_account(account_id)` — join across hospitals for "My appointments".

### 1c. Patient auth — JWT + deps
- `tools/auth.py`: `create_patient_token(*, account_id)` setting claim `kind="patient"` + `account_id`
  (distinct from staff tokens). Reuse encode/decode/blacklist.
- `api/deps.py`: `current_patient(credentials)` requiring `claims["kind"]=="patient"`. Staff
  `current_user`/`require_role` untouched → strict separation both directions.

### 1d. Patient API — `api/routes/patient_portal.py` (prefix `/patient`), registered in `api/app.py`
- `POST /patient/signup` (email, password ≥8 with complexity like `ClinicCreate`, name, phone) → token.
- `POST /patient/login` → token (add `/patient/login` to the `_login_hits` limiter branch in `api/app.py`).
- `GET /patient/me`, `GET /patient/hospitals` (`list_hospitals`),
  `GET /patient/hospitals/{id}/departments` (`list_departments`),
  `GET /patient/departments/{clinic_id}/doctors` (`get_doctors_for_clinic`),
  `GET /patient/appointments`.
- New schemas in `api/schemas.py`: `PatientSignup`, `PatientLogin`, `PatientAccountOut`,
  `PatientAppointmentOut`.

### 1e. Patient-scoped chat (text booking) — extend `api/routes/chat.py`
- `POST /patient/chat/stream` (requires `current_patient`): `{session_id, message, clinic_id}`.
  - Session id namespaced per account (e.g. `pt-<account_id>-...`), validated like today.
  - Calls `agent/runner.py:stream_turn_tokens(...)` (already supports `patient_name`/`patient_age`
    injection, line ~195); also pass chosen `clinic_id` and a new `patient_account_id` via run
    `config.configurable` (for memory in Phase 2).
  - On booking, get-or-create the hospital's `patients` row from the account → pass real `patient_id`
    to `book_appointment` (DB fn already accepts `patient_id`, line 189).
- `agent/state.py`: add `patient_id` and `patient_account_id` to `AppointmentState`; thread through
  `agent/runner.py:_turn_input`.

### 1f. Frontend — patient portal (Next.js, `appointment-ui/`)
The admin `AuthProvider` (`lib/auth.tsx`) force-redirects to `/login`; keep patient auth separate.
- Route group `app/(portal)/` with its own `PatientAuthProvider` (mirrors `lib/auth.tsx` but stores a
  distinct `patient_token` key and calls `/patient/me`). Pages: `/portal/signup`, `/portal/login`,
  `/portal` (hospital → department → doctor pickers), `/portal/book`, `/portal/appointments`.
- Refactor `app/chat/page.tsx` into a reusable `<BookingChat clinicId patientToken />` that posts to
  `/patient/chat/stream` with the bearer token (instead of anonymous `/chat/stream`).
- `lib/api.ts`: add `getPatientToken`/`setPatientToken`/`clearPatientToken` + patient API wrappers,
  reusing the existing `request<T>()` (error parsing already fixed).

### 1g. Voice booking for patients (web) — largest piece; can fast-follow text
- `/portal/book` "Talk to book" uses the LiveKit JS SDK to join a room; backend issues a LiveKit token
  embedding `patient_account_id` + `clinic_id` in room metadata.
- `main.py:_resolve_channel`/`session_handler` read that metadata to set `clinic_id` +
  `patient_account_id`, passed into `voice/langgraph_llm.py:LangGraphLLM` — same injection path as
  text, so memory (Phase 2) applies identically.

---

## Phase 2 — Agent long-term memory via `PostgresStore`

### 2a. Wire a store — `agent/graph.py`
- `from langgraph.store.postgres.aio import AsyncPostgresStore`; build once (reuse the existing
  `AsyncConnectionPool`), `await store.setup()`, and compile with
  `compile(checkpointer=checkpointer, store=store)`.
- **No vector index for memory** — use it as a plain namespaced KV store (`aget`/`aput` by key). Fully
  local/free; pgvector is reserved for RAG.

### 2b. Namespacing — `("patient_memory", str(patient_account_id))`
- `"profile"` → `{name, age, phone}`; `"preferences"` → `{preferred_doctor_id, preferred_time, language}`;
  `"visit:<appointment_id>"` → `{hospital_id, clinic_id, doctor_id, doctor_name, scheduled_at,
  serial_number}` (enumerate with `store.search`/`list_namespaces`).

### 2c. Read in — `agent/nodes.py:call_model_node`
- Read `patient_account_id` from run config (`from langgraph.config import get_store, get_config`),
  load profile/preferences/recent visits, add a `patient_context` block in `_prompt_context`
  ("Returning patient … last visit Dr. X … do NOT re-ask name/age"). Add `{patient_context}` slot to
  `agent/prompts.py:SYSTEM_PROMPT` defaulting to "" (backward compatible with anonymous/admin chats).

### 2d. Write out — `agent/nodes.py:post_booking_node` (runs only on success)
- Upsert `profile` + `preferences`, append `visit:<id>` via `get_store()`. Keeps the
  `book_appointment` tool focused on the DB write.

### 2e. Optional HITL — doctor approval (plan-only)
- Per-department opt-in flag on `clinics`; a node calls `interrupt({...})` before confirming; doctor
  approves (WhatsApp/dashboard); resume with `Command(resume=...)`.

---

## Phase 3 (future) — RAG over per-hospital documents (pgvector)

Source: `https://docs.langchain.com/oss/python/integrations/vectorstores/index#pgvector`

**Dependencies:** `pip install langchain-postgres psycopg[binary]`

1. **Migration** — `CREATE EXTENSION IF NOT EXISTS vector;` + new `hospital_documents` table
   (tracks raw uploads: `id, hospital_id, filename, chunk_count, created_at`).

2. **Local/free embeddings** — `OllamaEmbeddings(model="nomic-embed-text",
   base_url=settings.ollama_base_url)` (reuses existing `settings.ollama_base_url`).
   Add `embedding_model: str = "nomic-embed-text"` to `config.py`.

3. **Vector store** (`tools/rag.py`):
   ```python
   from langchain_postgres import PGVector
   from langchain_community.embeddings import OllamaEmbeddings

   def get_vector_store(hospital_id: int) -> PGVector:
       embeddings = OllamaEmbeddings(model=settings.embedding_model,
                                     base_url=settings.ollama_base_url)
       return PGVector(
           embeddings=embeddings,
           collection_name="hospital_docs",
           connection=settings.database_url.replace("asyncpg", "psycopg"),
           use_jsonb=True,   # confirmed by docs: enables metadata filtering
       )
   ```
   Ingest with `store.add_documents(chunks, ids=[...])` where each chunk carries
   `metadata={"hospital_id": hospital_id}`. Search with filter
   `{"hospital_id": {"$eq": hospital_id}}` — psycopg3 metadata operators `$eq`, `$in`, `$and`.

4. **Ingestion API** — `api/routes/documents.py` (`POST/GET/DELETE /hospitals/{id}/documents`,
   `hospital_admin`/`platform_admin` only): upload file → chunk with `RecursiveCharacterTextSplitter`
   → `add_documents`. Record in `hospital_documents`.

5. **Agent tool** — `search_hospital_info(query: str)` in `agent/tools.py`, scoped via
   `hospital_id` from state. Returns top-k chunk text. Only added to `TOOLS` when the hospital
   has at least one document (checked at graph-build time or via a state flag).

## Phase 4 (future) — Tools as an MCP server

Source: `https://docs.langchain.com/oss/python/langchain/mcp`

**Dependencies:** `pip install langchain-mcp-adapters mcp`

1. **FastMCP server** — `mcp_server/booking.py` (separate entrypoint, reuses `tools/database.py`):
   ```python
   from mcp.server.fastmcp import FastMCP
   mcp = FastMCP("booking")

   @mcp.tool()
   async def get_available_slots(clinic_id: int, date: str) -> list[dict]: ...

   @mcp.tool()
   async def book_appointment(clinic_id: int, patient_name: str, ...) -> dict: ...
   ```
   Run as subprocess: `python -m mcp_server.booking` (stdio) or
   `uvicorn mcp_server.booking:app --port 8001` (HTTP).

2. **Load into graph** — in `agent/graph.py` startup, use as a context manager so the subprocess
   lives for the app lifetime:
   ```python
   from langchain_mcp_adapters.client import MultiServerMCPClient

   async with MultiServerMCPClient({
       "booking": {
           "transport": "stdio",
           "command": "python",
           "args": ["-m", "mcp_server.booking"],
       }
   }) as mcp_client:
       mcp_tools = await mcp_client.get_tools()  # returns LangChain BaseTool list
       graph = build_graph(extra_tools=mcp_tools)
   ```
   Note: **stateless by default** — each tool invocation creates a fresh `ClientSession`. Persistent
   sessions available via `async with client.session("booking") as session:` for high-throughput use.
   Keep transport local (stdio or `http://localhost:8001/mcp`) to stay fully local/free.

---

## Verification

**Phase 1**
1. `alembic upgrade head` applies `0015`; `patient_accounts` + `patients.account_id` exist.
2. `POST /patient/signup` → 201 + token; duplicate email → 409 (clean message). `login` bad creds → 401.
3. Portal: sign up → hospital → department → doctor → chat books; appointment has correct `clinic_id`,
   `doctor_id`, and a `patient_id` linked to the account; shows under `/portal/appointments` and the
   hospital admin dashboard.
4. Patient token rejected by `require_role`; staff token rejected by `current_patient`.
5. `pytest` (~70 tests) green; anonymous `/chat` still works (new state fields optional,
   `patient_context` defaults "").

**Phase 2**
6. Book once, start a fresh session — agent greets by name, does not re-ask name/age (profile from
   `PostgresStore`); store rows exist under `("patient_memory","<account_id>")`.
7. Per-account isolation: account B never sees account A's data.
8. Same over voice once 1g lands.

**Phases 3–4** — own acceptance when scheduled (agent answers a policy question from an uploaded doc;
an MCP client lists/calls the booking tools).

# Scaling & agentic features

How to run this beyond a single process, and what the agent can now do beyond
booking. Everything here is local/free — no paid services required.

## Ollama CPU tuning (do this first — it dominates response latency)

Ollama's defaults are wrong for this workload on most machines:

- **`OLLAMA_NUM_THREAD`** — Ollama's automatic thread count only counts
  performance cores on hybrid CPUs (e.g. Intel's P-core/E-core laptop chips),
  which can leave most of the machine idle. Set it explicitly to your host's
  physical core count minus 2-4 (reserved for the API process, STT/TTS, and
  the voice worker). Measured on a 2P+10E-core laptop: 2 threads (auto) →
  ~4 tok/s, 10 threads (explicit) → ~19 tok/s. Set via `.env`'s
  `OLLAMA_NUM_THREAD` (read by `agent/nodes.py`'s `ChatOllama` construction) —
  this one is host-specific, so it is **not** set in `docker-compose.yml`.
- **`OLLAMA_NUM_PARALLEL=1`** — with more than 1 parallel slot, Ollama cannot
  reuse the previous turn's prompt-prefix KV cache, so every turn re-prefills
  the full system prompt + conversation history from scratch. Measured
  impact: ~70s per turn (NUM_PARALLEL=6, the Ollama default) vs ~2-4s on a
  warm thread (NUM_PARALLEL=1). This repo's `docker-compose.yml` sets it on
  the `ollama` service; if running Ollama directly on the host (not in
  Docker), set it in the systemd unit override
  (`/etc/systemd/system/ollama.service.d/override.conf`) or via
  `OLLAMA_NUM_PARALLEL=1 ollama serve`.

The trade-off: a single parallel slot serializes concurrent patients — the
`OLLAMA_MAX_CONCURRENT` semaphore below already limits how many turns queue
up per worker, so this only matters if you also raise that above 1.

## Running multiple API workers

**`docker compose up` is multi-worker by default in production.** The backend
ships with `WEB_CONCURRENCY=4`, the bundled `redis` service for shared rate
limiting, and `DB_POOL_MAX=10` — a self-consistent set that stays under stock
Postgres's 100-connection limit (4 × 2 × 10 = 80; see the budget note below).
Override any of them in `.env`; drop to a single worker with the in-process
limiter via `WEB_CONCURRENCY=1` and an empty `REDIS_URL`.

```bash
docker compose up backend                        # 4 workers + redis (default)
WEB_CONCURRENCY=8 DB_POOL_MAX=6 docker compose up backend   # 8 × 2 × 6 = 96
uvicorn api.app:app --workers 2                  # outside compose: set REDIS_URL yourself
```

- **Reminders** are multi-worker safe out of the box: the hourly loop takes a
  Postgres advisory lock (`api/app.py::_reminder_loop`), so exactly one worker
  sends each cycle.
- **Rate limiting** is shared across workers via the bundled redis service by
  default (`REDIS_URL` is injected by `docker-compose.yml`). It **fails open**
  to a per-worker in-process counter if redis is unreachable (`api/ratelimit.py`),
  so a redis blip never takes the API down — it just loosens the limit briefly.
  Outside compose with >1 worker, set `REDIS_URL=redis://localhost:6379/0`
  yourself.
- **LLM throughput**: each worker holds its own Ollama semaphore
  (`OLLAMA_MAX_CONCURRENT`, default 4). The box's real ceiling is CPU
  inference; when all slots are busy the patient sees an honest
  "এজেন্ট ব্যস্ত…" status instead of a frozen spinner.
- **Checkpointer pool** sizes follow `DB_POOL_MIN`/`DB_POOL_MAX` (shared with
  the data pool settings).

### Watch your Postgres connection budget

Each API worker opens **two** pools — the asyncpg data pool and the psycopg
LangGraph-checkpointer pool — and each is sized `DB_POOL_MAX` (production
default **10**). So one worker can hold up to **~20 connections**, and the whole
deployment needs:

```
WEB_CONCURRENCY × 2 × DB_POOL_MAX   ≤   Postgres max_connections
```

Postgres defaults to `max_connections = 100`. The shipped defaults
(`WEB_CONCURRENCY=4`, `DB_POOL_MAX=10`) sit at **4 × 2 × 10 = 80** — comfortably
under the limit with headroom for the voice worker. Going past that starts
refusing connections with `FATAL: sorry, too many clients already` — a failure
that only shows up under load in production, not in a single-worker dev run.
To scale further, do one of:

- **Lower `DB_POOL_MAX`** as you add workers — e.g. `WEB_CONCURRENCY=8` needs
  `DB_POOL_MAX=6` (8 × 2 × 6 = 96).
- **Raise Postgres `max_connections`** (each connection costs a few MB of RAM).
- **Put PgBouncer in front** (transaction pooling) and point both pools at it —
  the checkpointer pool already uses `prepare_threshold=0`
  (`agent/graph.py`), which is required for PgBouncer transaction mode.

The voice worker (separate process) opens its own pools too — count it as an
extra "worker" in the formula if it shares the same Postgres.

## RAG backend

`RAG_BACKEND=pgvector` (default) stores document chunks + embeddings in the
`rag_chunks` Postgres table (migration 0020), shared by every worker. Requires
the pgvector extension:

```bash
sudo apt install postgresql-<major>-pgvector   # host Postgres
# docker-compose already uses the pgvector/pgvector:pg16 image
```

`RAG_BACKEND=chroma` keeps the legacy per-process file store as a rollback
path for one release.

## Voice worker scaling

The LiveKit worker pool model load-balances jobs across replicas — scale by
running more `python main.py start` processes. `VOICE_LOAD_THRESHOLD` defaults
to infinity (never refuse) so a single-worker box doesn't hang calls at
"connecting" whenever the LLM pegs the CPU — set it to e.g. `0.7` only once you
run multiple replicas, so a busy worker sheds new calls to another. Silero VAD
is prewarmed
per job process (`main.py::_setup_process`). LiveKit's guidance: a 4-core/8GB
worker handles roughly 10–25 concurrent calls.

## Fault tolerance

Graph nodes carry LangGraph retry policies (`agent/graph.py`): transient
Ollama/DB failures retry with backoff instead of failing the patient's turn.
Interrupts (confirm questions) are never retried.

## Agentic features (and where they surface)

- **Manage appointments in chat/voice** — authenticated patients can list,
  cancel, and reschedule via the agent (`agent/tools.py`). Destructive actions
  pause on a durable LangGraph `interrupt()`: the portal shows a confirm card
  (yes/no buttons), voice asks the question aloud and interprets the spoken
  answer. The pause survives page reloads (checkpointer).
- **Semantic patient memory** — visit summaries are embedded (local
  `nomic-embed-text`) into the LangGraph store; "গতবারের ডাক্তার" style
  references recall the right visit, and returning patients get a one-tap
  "ডা. X-এর কাছে আবার বুক করুন" chip.
- **Human escalation** — the agent calls `request_human_help` when a patient
  wants a person; open escalations appear as a "Needs attention" queue on the
  admin Conversations page, where staff can reply (SMS/WhatsApp send, or
  message injected into the portal chat thread) and mark resolved.
- **Two-way reminders** — the 24h reminder asks "উত্তর দিন: ১ = নিশ্চিত,
  ২ = বাতিল"; replies are handled deterministically without an LLM turn
  (`tools/reminders.py::handle_reminder_reply`) and the admin appointments
  table shows a "রোগী নিশ্চিত" chip.

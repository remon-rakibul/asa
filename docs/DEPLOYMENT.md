# Deployment (Docker)

The whole application is containerized. `docker compose up` gives you:

| Service    | Image                                | Port (host)              | Started by default? | What it is |
|------------|--------------------------------------|--------------------------|---------------------|------------|
| `postgres` | `pgvector/pgvector:pg16`             | `POSTGRES_PORT` (5432)   | ✅ yes | Appointments, patients, reviews, LangGraph checkpoints, RAG vectors |
| `backend`  | built from `Dockerfile`              | `BACKEND_PORT` (8000)    | ✅ yes | FastAPI API + agent; runs `alembic upgrade head` on start |
| `ui`       | built from `appointment-ui/Dockerfile` | `UI_PORT` (3000)       | ✅ yes | Next.js admin console + patient portal (standalone build) |
| `ollama`   | `ollama/ollama:latest`               | `OLLAMA_HOST_PORT` (11434) | ⛔ profile `local-llm` | Local LLM server; pulls `OLLAMA_MODEL` on first start |
| `voice`    | built from `Dockerfile.voice`        | —                        | ⛔ profile `voice`  | LiveKit voice worker (browser calls + SIP telephony) |

> **Where does the LLM come from?** By default the backend talks to an Ollama
> reachable at `DOCKER_OLLAMA_URL` (default `http://host.docker.internal:11434`
> — i.e. an Ollama **already running on the host**). The containerized `ollama`
> service is opt-in (profile `local-llm`) so it doesn't clash with a host
> Ollama or re-download a multi-GB model. Pick one of the two paths in §1 below.

Schema is owned by **Alembic** — the backend container migrates the database
automatically before serving, so a fresh volume becomes a fully migrated DB
with no manual steps.

---

## 1. One-time setup

```bash
cp .env.example .env        # then edit
```

Required in `.env` before the stack will start:

```bash
POSTGRES_PASSWORD=<strong random value>       # compose refuses to start without it
JWT_SECRET=<strong random value>              # e.g. openssl rand -hex 32
OLLAMA_MODEL=gemma4:latest
```

**Then pick how the backend reaches the LLM:**

- **A — containerized Ollama (self-contained box, no host Ollama):** the stack
  runs its own Ollama and pulls the model. Add to `.env`:
  ```bash
  DOCKER_OLLAMA_URL=http://ollama:11434
  ```
  and always include `--profile local-llm` when starting (see §2).
- **B — reuse a host Ollama (the default):** leave `DOCKER_OLLAMA_URL` at its
  default (`http://host.docker.internal:11434`) and make sure `ollama serve` is
  running on the host with `OLLAMA_HOST=0.0.0.0` so the container can reach it.
  Do **not** pass `--profile local-llm`.

Everything else (WhatsApp, Twilio, LiveKit, Gemini fallback, TTS/STT engines)
is optional and documented in `.env.example` and the README's
[Environment variables](../README.md#environment-variables) table.

## 2. Start / stop

```bash
# Path A — self-contained (also starts + pulls the containerized LLM):
docker compose --profile local-llm up --build -d

# Path B — reuse host Ollama (default; ollama service stays off):
docker compose up --build -d

docker compose logs -f backend      # watch migrations + LLM warmup
docker compose down                 # stop (data volumes survive)
docker compose down -v              # stop AND DELETE data — irreversible
```

or via the Makefile: `make up`, `make logs`, `make down`.

With Path A, first start is slow: the `ollama` container downloads the model
(~5 GB for gemma4) into the `ollama_data` volume. Subsequent starts are fast.
With Path B the model is already loaded on the host, so startup is quick.

Verify:

```bash
curl http://localhost:8000/health   # {"status":"ok","checks":{"database":"ok","ollama":"ok"}}
```

Then create the first admin login:

```bash
docker compose exec backend python -m scripts.create_admin \
    --email admin@yourhospital.bd --password '<password>'
```

- Admin console: `http://localhost:3000`
- Patient portal: `http://localhost:3000/portal`
- API docs (Swagger): `http://localhost:8000/docs`

## 3. Voice worker (optional profile)

Browser voice calls and SIP telephony need the LiveKit worker. Its image is
large (CPU torch + Whisper STT + neural Bangla TTS), so it's behind a compose
profile:

```bash
# needs LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in .env
docker compose --profile voice up --build -d voice
```

Model weights (Whisper, TTS) download on first start into the `voice_models`
volume. The worker has **no hot reload** — restart it after backend/agent code
changes: `docker compose restart voice`.

Inbound phone calls to the platform number run the cross-hospital platform
agent, gated to premium/trial patients by one-time-verified caller-ID
(`VOICE_PREMIUM_GATE=true`, the default) — see
[TELEPHONY.md](./TELEPHONY.md#who-can-call-the-premium-gate). That
verification is delivered by SMS, so **a working SMS provider
(`SMS_PROVIDER` + its credentials) is required in production** for patients
to be able to verify and call.

For local development you can keep running it on the host instead:
`python main.py dev` (see [VOICE_WEB.md](./VOICE_WEB.md)). Console-mode
testing (`python main.py console`) has no caller-ID, so set
`VOICE_PREMIUM_GATE=false` locally or the gate will decline you.

## 4. Running next to a local dev setup

The compose file parameterizes every host port, and the backend can reuse an
Ollama that's already running on the host (avoids a second multi-GB model
download and doubling RAM). Add to `.env`:

```bash
POSTGRES_PORT=5433                                    # host Postgres keeps 5432
BACKEND_PORT=8001
UI_PORT=3001
NEXT_PUBLIC_API_URL=http://localhost:8001             # UI build must point at the docker backend
DOCKER_OLLAMA_URL=http://host.docker.internal:11434   # reuse host Ollama
# The API only accepts browser requests from listed origins — without the 3001
# entries every portal/admin call from the dockerized UI fails ("Failed to fetch").
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001
```

then start the stack (plain `up` already leaves the `ollama` service off, so it
reuses your host Ollama — this is Path B from §1):

```bash
docker compose up --build -d
```

The dockerized stack gets its own empty database (fresh volume, migrated by
Alembic) — it does not touch your host Postgres.

## 5. Production checklist

- [ ] `JWT_SECRET` and `POSTGRES_PASSWORD` set to strong random values (never the defaults).
- [ ] HTTPS: put a reverse proxy (Caddy / nginx / Traefik) in front of the
      `ui` (3000) and `backend` (8000). The browser mic (voice calls) and
      secure cookies require HTTPS in production.
- [ ] **`TRUST_PROXY_HEADERS=true`** once the reverse proxy is in place, and
      make the proxy set `X-Forwarded-For`. Without it the backend sees only
      the proxy's IP, so **every client shares one rate-limit bucket** — 10
      total logins/min and 60 total patient requests/min across your whole user
      base, locking real users out with 429s. (Leave it `false` only if the
      backend is exposed directly, where the header is forgeable.)
- [ ] `NEXT_PUBLIC_API_URL` set to the **public** API URL before building the
      `ui` image — it's baked in at build time.
- [ ] `CORS_ORIGINS` includes the public UI origin(s) — the API rejects
      browser requests from unlisted origins.
- [ ] Webhooks: point Meta (WhatsApp) and Twilio at
      `https://<api-domain>/whatsapp/webhook` and `/twilio/sms` — see
      [WHATSAPP_AND_SMS.md](./WHATSAPP_AND_SMS.md) and
      [TELEPHONY.md](./TELEPHONY.md).
- [ ] Telephony (if using the platform phone number): SIP trunk + individual
      dispatch rule into LiveKit, the voice worker running, a working SMS
      provider (phone-verification OTPs), and `VOICE_PREMIUM_GATE=true` —
      see [TELEPHONY.md](./TELEPHONY.md).
- [ ] Payments (if charging — see [MONETIZATION.md](./MONETIZATION.md#setting-up-sslcommerz-sandbox--production)
      for the full SSLCommerz sandbox → production walkthrough): set
      `PUBLIC_BASE_URL` / `PORTAL_BASE_URL` to the **public** HTTPS URLs so the
      gateway's IPN/redirect callbacks reach you. For a real gateway set
      `PAYMENT_PROVIDER=sslcommerz` + `SSLCOMMERZ_STORE_ID`/`SSLCOMMERZ_STORE_PASSWD`
      (live credentials, not the sandbox ones) and `SSLCOMMERZ_SANDBOX=false` for
      production. The default `PAYMENT_PROVIDER=manual` needs no gateway; set
      `PAYMENT_MANUAL_AUTOPAY=false` in production so bookings hold until an
      admin marks them paid.
- [ ] Scaling: `WEB_CONCURRENCY>1` requires `REDIS_URL` (uncomment the redis
      service) so rate limits are shared — details in [SCALING.md](./SCALING.md).
      Keep `OLLAMA_NUM_PARALLEL=1` per Ollama instance (prompt-cache reuse).
- [ ] Backups: `docker compose exec postgres pg_dump -U postgres appointments > backup.sql`
      on a cron. The `postgres_data`, `ollama_data`, and `voice_models`
      volumes are the only state.
- [ ] Hardware: CPU-only works but is slow (laptop-class ≈ 5–8 tok/s). A
      single 12 GB+ NVIDIA GPU transforms latency; add the standard
      `deploy.resources.reservations.devices` GPU stanza to the `ollama`
      service and install the NVIDIA container toolkit.

## 6. Updating a deployment

```bash
git pull
docker compose build backend ui          # voice too, if you run the profile
docker compose up -d                     # backend re-runs alembic upgrade head
docker compose --profile voice up -d voice
```

Migrations are forward-only and applied automatically by the backend on start.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `set POSTGRES_PASSWORD` error at startup | Add `POSTGRES_PASSWORD=…` to `.env` |
| Port already in use | Set `POSTGRES_PORT` / `BACKEND_PORT` / `UI_PORT` / `OLLAMA_HOST_PORT` in `.env` (§4) |
| `/health` shows `"ollama":"error"` | Path A: model still downloading — `docker compose logs -f ollama`. Path B: host Ollama not running or not bound to `0.0.0.0` (the container can't reach `127.0.0.1`), or `--profile local-llm` was forgotten with no host Ollama present |
| First reply takes minutes | Cold model load + CPU prefill; the backend pre-warms on start, subsequent turns are much faster |
| UI calls the wrong API URL | `NEXT_PUBLIC_API_URL` is baked at build time — set it, then `docker compose build ui` |
| Browser shows "Failed to fetch" but `curl` works | UI origin missing from `CORS_ORIGINS` in `.env` — add it, then `docker compose up -d backend` |
| Voice worker joins no rooms | Check `LIVEKIT_*` in `.env`; restart after backend changes (no hot reload) |

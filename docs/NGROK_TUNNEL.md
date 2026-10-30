# Webhook tunnel (ngrok)

Meta (WhatsApp), SMS gateways and SSLCommerz deliver events to your backend
over **public HTTPS**. Until the backend has a real domain (e.g.
`api.yourdomain.com`), an ngrok tunnel gives it one:

```
WhatsApp user ─► Meta ─► https://<NGROK_DOMAIN>/whatsapp/webhook ─► ngrok ─► backend:8000
```

The tunnel URL is a **fixed ngrok domain**, so the Callback URL in Meta never
has to change — just start the tunnel again.

> **One tunnel at a time.** A domain can be served by only one running ngrok.
> Use either the Docker way **or** the host way below, never both. A second
> ngrok fails with `ERR_NGROK_334` (endpoint already online).

---

## One-time setup

1. Sign in at <https://dashboard.ngrok.com> (free plan is enough).
2. **Your Authtoken** → copy it. **Domains** → claim your free static domain
   (e.g. `aviation-effort-darling.ngrok-free.dev`).
3. Add both to `.env`:
   ```
   NGROK_AUTHTOKEN=<your authtoken>
   NGROK_DOMAIN=aviation-effort-darling.ngrok-free.dev   # no https://
   ```
4. In **developers.facebook.com → your app → WhatsApp → Configuration**
   (Use cases → Customize → Step 2. Production setup → Configure Webhooks):
   - **Callback URL:** `https://<NGROK_DOMAIN>/whatsapp/webhook`
   - **Verify token:** the value of `WHATSAPP_VERIFY_TOKEN` in `.env`
   - **Verify and save**, then subscribe the **messages** field
     (and **calls** for WhatsApp calling — see WHATSAPP_CALLING.md).
5. Link the WhatsApp Business Account to your app (only once — without this
   Meta sends messages to its own test app, not yours):
   ```
   docker compose exec backend python -c "import httpx;from config import settings as s;print(httpx.post('https://graph.facebook.com/v21.0/<WABA_ID>/subscribed_apps',headers={'Authorization':'Bearer '+s.whatsapp_token}).json())"
   ```
   `WABA_ID` is the "Messaging account ID" on the app's *Step 1. Try it out* page.

---

## Running with Docker (recommended)

The `ngrok` service lives in `docker-compose.yml` under the opt-in `tunnel`
profile, so plain `docker compose up` / `make up` never starts it.

| Task | Command |
|---|---|
| Start the tunnel | `make tunnel` |
| Is it up? Print URLs | `make tunnel-status` |
| Follow ngrok logs | `make tunnel-logs` |
| Stop the tunnel | `make tunnel-down` |
| Inspect requests (browser) | <http://127.0.0.1:4040> |

`make tunnel` waits for the backend to be healthy, starts ngrok, then checks
`https://<NGROK_DOMAIN>/health/live` through the tunnel and prints:

```
Public URL:  https://aviation-effort-darling.ngrok-free.dev
Webhook:     https://aviation-effort-darling.ngrok-free.dev/whatsapp/webhook
Inspector:   http://127.0.0.1:4040
Tunnel:      UP (backend reachable)
```

Without make:

```bash
docker compose --profile tunnel up -d ngrok      # start
docker compose --profile tunnel logs -f ngrok    # logs
docker compose --profile tunnel stop ngrok       # stop
```

Inside Docker, ngrok talks to the backend over the compose network
(`backend:8000`), so `BACKEND_PORT` on the host doesn't matter. The service
has `restart: unless-stopped`: it comes back after a reboot / Docker restart
until you run `make tunnel-down`.

Full stack including the tunnel: `make up && make tunnel`.

**Start it automatically with everything else:** add `tunnel` to
`COMPOSE_PROFILES` in `.env`:

```
COMPOSE_PROFILES=voice,tunnel
```

Then `make up` / `docker compose up -d` starts postgres, redis, backend, voice,
ui **and** ngrok, and `make down` stops them all. `make tunnel-down` still stops
just the tunnel.

---

## Running on the host (without Docker)

Use this when the backend runs outside Docker (e.g. `uvicorn` on your machine).
Point ngrok at the port the backend listens on (`BACKEND_PORT`, `8001` here):

```bash
ngrok http 8001 --url=https://aviation-effort-darling.ngrok-free.dev   # start (foreground; Ctrl+C stops)
```

Background start / stop:

```bash
nohup ngrok http 8001 --url=https://$NGROK_DOMAIN --log=stdout > /tmp/ngrok.log 2>&1 &
pkill -f "ngrok http"                                                    # stop
curl -s localhost:4040/api/tunnels | python3 -m json.tool               # current URL
```

The host ngrok reads its token from `ngrok config add-authtoken <token>`
(stored in `~/.config/ngrok/ngrok.yml`, or `~/snap/ngrok/...` for the snap).

---

## Checking it works

```bash
make tunnel-status                         # tunnel + backend reachable
make verify                                # WhatsApp token / phone id are valid
docker compose logs -f backend | grep whatsapp
```

Send a WhatsApp message to the business number. In the backend log you should see:

```
"POST /whatsapp/webhook HTTP/1.1" 200 OK                       ← message in
POST https://openrouter.ai/...  200 OK                           ← agent thinking
POST https://graph.facebook.com/.../messages "HTTP/1.1 200 OK"   ← reply out
"POST /whatsapp/webhook HTTP/1.1" 200 OK                       ← delivered/read receipts
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `POST /whatsapp/webhook 403` | `WHATSAPP_APP_SECRET` is wrong or a placeholder — copy it from *App settings → Basic → App secret*, restart backend (`docker compose up -d backend`). |
| Meta "Verify and save" fails | Tunnel down (`make tunnel-status`) or `WHATSAPP_VERIFY_TOKEN` differs from what you typed in Meta. |
| No `POST /whatsapp/webhook` at all | WABA not linked to your app (setup step 5), **messages** field not subscribed, or the sender isn't an allowed test recipient. |
| `ERR_NGROK_334` in tunnel logs | Another ngrok (host or container) already serves the domain — stop it (`pkill -f "ngrok http"` / `make tunnel-down`). |
| `ERR_NGROK_4018` / auth error | `NGROK_AUTHTOKEN` missing or wrong in `.env`. |
| Nothing in or out; sends fail with `(#133010) Account not registered`; `make verify` says the number is PENDING | Meta un-registered the number (common with Meta test numbers). Run `make whatsapp-register` (uses `WHATSAPP_REGISTRATION_PIN`). |
| Reply sent but outbox shows `failed` with 401 | `WHATSAPP_TOKEN` expired/revoked — generate a permanent System User token. |

## Going to production

Replace the tunnel with a real domain: point `api.<yourdomain>` at the server
behind TLS (Caddy/nginx/Cloudflare), set `PUBLIC_BASE_URL=https://api.<yourdomain>`,
update the Callback URL in Meta, and run `make tunnel-down`.

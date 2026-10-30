# WhatsApp voice calls → ASA voice agent

Patients can tap the **call button** in the ASA WhatsApp chat and talk to the
same Bangla voice agent as the phone line and the web portal — search doctors,
book, cancel, reschedule. Inbound (user-initiated) WhatsApp calls are free.

```
Patient taps 📞 in WhatsApp
   │
   ▼
Meta ──(webhook field "calls", event=connect, SDP offer)──► /whatsapp/webhook  (backend)
                                                              │ tools/whatsapp_calls.py
                                                              ▼
                                               LiveKit AcceptWhatsAppCall (Connector)
                                                 · answers the call with Meta
                                                 · bridges audio into room wa-call-…
                                                 · dispatches agent "appointment-setter"
                                                   with JSON metadata (scope + caller)
                                                              ▼
                                               voice worker (main.py) → LangGraph agent
Patient hangs up → Meta "terminate" → backend → DisconnectWhatsAppCall(USER_INITIATED)
```

## Who the caller is (same rules as WhatsApp messages)

Resolved in the backend by `api/messaging.resolve_message_scope`:

| Situation | Scope | Conversation thread |
|---|---|---|
| Called a hospital's own WhatsApp number (mapped in `channels`, kind `whatsapp`) | that clinic | `whatsapp-<number>` |
| Called the ASA number, phone **verified** on a patient account | platform (all hospitals) | `pt-acc<id>-platform` — same as their web chat, calls and WhatsApp chat |
| Called the ASA number, unknown phone | platform | `whatsapp-<number>` — continues their WhatsApp chat |

The premium gate for the phone line (`VOICE_PREMIUM_GATE`) does **not** apply
to WhatsApp calls. Live transfer to reception is phone-line only (WhatsApp calls
can't be SIP-transferred); `request_human_help` alerts staff instead.

## Setup checklist

1. **LiveKit Cloud** project (connectors don't run on self-hosted LiveKit).
   `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env`. If
   `AcceptWhatsAppCall` fails with a permission/feature error, ask LiveKit to
   enable the WhatsApp Connector for the project.
2. **WhatsApp messaging works first** — token, phone id, app secret, webhook
   (see [WHATSAPP_AND_SMS.md](WHATSAPP_AND_SMS.md) and [NGROK_TUNNEL.md](NGROK_TUNNEL.md)).
3. **Enable calling on the number** (one-time API call):
   ```bash
   docker compose exec backend python -c "
   import httpx; from config import settings as s
   u=f'https://graph.facebook.com/v25.0/{s.whatsapp_phone_id}/settings'
   h={'Authorization':'Bearer '+s.whatsapp_token}
   print(httpx.post(u,headers=h,json={'calling':{'status':'ENABLED','call_icon_visibility':'DEFAULT','callback_permission_status':'ENABLED'}}).json())
   print(httpx.get(u,headers=h).json())"
   ```
   Meta documents a messaging-limit requirement (≥ 2000) for calling on
   production numbers; if the POST is rejected, raise the tier first
   (business verification + quality sending).
4. **Subscribe the `calls` webhook field** — Meta app → WhatsApp →
   Configuration → Webhook fields → `calls` → Subscribe, same version as
   `messages` (v25.0).
5. `.env` (defaults shown):
   ```
   WHATSAPP_CALLING_ENABLED=true
   WHATSAPP_CLOUD_API_VERSION=25.0     # must match the webhook field version
   ```
6. Rebuild/restart: `docker compose up -d --build backend voice`.
7. `make verify` → the WhatsApp row shows `calling enabled`.

Optional: business hours for calls (`call_hours` in the same settings call) —
outside them WhatsApp offers the patient a callback request instead.

## ASA calls the patient (business-initiated)

When the call button isn't shown (e.g. a reset Meta test number), ASA can
call the patient instead — the same voice agent and conversation thread:

```bash
docker compose exec backend python -m scripts.whatsapp_call_patient 8801XXXXXXXXX
```

The patient's WhatsApp rings; when they answer, Meta sends a `calls`
connect webhook (direction `BUSINESS_INITIATED`) with the SDP answer, and the
backend completes it with LiveKit `ConnectWhatsAppCall`
(`tools/whatsapp_calls.py::dial_patient` / `connect_outbound_call`).

Requires the patient's **call permission** for the business number (check:
`GET /<PHONE_NUMBER_ID>/call_permissions?user_wa_id=<number>`; Meta grants
temporary permission after recent interaction, otherwise send a call
permission request). Meta rate-limits business-initiated calls and permission
requests, and business-initiated calling isn't available in every country.

## Testing

From a phone allowed to message the number (a test recipient while the app is
in development), open the chat with the ASA number and tap the call icon. If
the icon is missing, update WhatsApp and reopen the chat (it can take a few
minutes after enabling).

```bash
docker compose logs -f backend | grep -iE "whatsapp call|AcceptWhatsAppCall"
docker compose logs -f voice   | grep -iE "wa-call|session"
```

Expected backend log: `WhatsApp call wacid… accepted into room wa-call-… (platform=True, account=…)`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No call icon in WhatsApp | Calling not ENABLED (step 3) or `call_icon_visibility` not DEFAULT; update/restart WhatsApp. |
| Phone rings forever, no `calls` webhook in logs | `calls` field not subscribed (step 4) or tunnel down (`make tunnel-status`). |
| Webhook arrives, log says `LiveKit AcceptWhatsAppCall failed` | Connector not enabled for the LiveKit project, wrong LiveKit creds, or `WHATSAPP_CLOUD_API_VERSION` unsupported (use 23–26). |
| `calling is not configured` in log | `WHATSAPP_CALLING_ENABLED=false` or WhatsApp/LiveKit creds missing. |
| Call connects but agent silent | Voice worker not running / not registered as `appointment-setter` (`docker compose logs voice`). |

## Code

- `tools/whatsapp_calls.py` — accept / end call, dispatch metadata
- `api/routes/whatsapp.py::_queue_call` — routes `calls` webhook events (de-duplicated)
- `main.py::_resolve_channel` — `whatsapp_call` metadata branch; `_voice_session_id` thread reuse
- Tests: `tests/test_whatsapp_calls.py`, `tests/test_voice_worker.py`

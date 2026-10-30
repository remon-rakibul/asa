# WhatsApp & SMS channels

Both reuse the same LangGraph agent, and each is inert until configured.

## Routing: one ASA number, platform mode

ASA has **one** WhatsApp number and **one** SMS number for the whole
marketplace, like the platform voice line (`api/messaging.py`):

- **The ASA number** (any business number with no `channels` row) runs the
  agent in **platform mode**. It searches doctors in every hospital and books
  anywhere.
- **A verified sender** (the patient verified their phone once via OTP on
  `/portal/account`) joins their account thread `pt-acc{id}-platform`. That's
  the same conversation as their portal chat and voice calls. Bookings link to
  the account, and cancel/reschedule work by message: the agent asks to
  confirm, and the next reply ("হ্যাঁ") confirms.
- **Anyone else** gets an anonymous platform thread keyed on their number.
  They can search and book.
- **A hospital's own number**, mapped with a `channels` row (below), keeps
  that clinic's scope.

Turns from one sender are processed one at a time (in-process lock plus a
Postgres advisory lock), so two quick messages can't race on the same
thread. `MESSAGING_PLATFORM_MODE=false` restores the old behaviour, where
unmapped numbers fall back to the default clinic.

## WhatsApp (Meta Cloud API)

1. Create a Meta app + WhatsApp Business account; get a **phone number ID** and a
   **permanent access token**. Choose a **verify token** (any secret string).
2. Set in `.env`:
   ```
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_ID=...
   WHATSAPP_VERIFY_TOKEN=...
   ```
3. (No public domain yet? Run the ngrok tunnel: `make tunnel` — see
   [NGROK_TUNNEL.md](NGROK_TUNNEL.md).) In the Meta dashboard, set the webhook callback URL to
   `https://<host>/whatsapp/webhook` and the verify token above. Subscribe to
   `messages`.
4. Map the business number to a clinic:
   ```sql
   INSERT INTO channels (clinic_id, kind, identifier)
   VALUES (<clinic_id>, 'whatsapp', '<display_phone_number from webhook>');
   ```

- **Text** messages are answered directly.
- **Voice notes** are downloaded and transcribed according to
  `WHATSAPP_VOICE_NOTE_STT` (`auto` | `gemini` | `whisper` | `off`). With
  `auto`, Gemini is used when `GEMINI_API_KEY` is set; note that the recording
  then leaves this server. Otherwise local faster-whisper is used if it's
  installed (it isn't in the default backend image). If a note can't be
  transcribed, the agent itself asks the patient to type, so the patient is
  never left without a reply.
- Outbound confirmations/replies use `send_whatsapp_text`.

## SMS

Two providers, selected by `SMS_PROVIDER`:

- `twilio` (default): set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `TWILIO_FROM_NUMBER`. Inbound webhook: `POST /twilio/sms`.
- `bdgateway`: a local BD HTTP SMS gateway (e.g. SSL Wireless) — cheaper and
  supports Bangla Unicode + masked sender IDs. Set:
  ```
  SMS_PROVIDER=bdgateway
  BD_SMS_API_URL=...
  BD_SMS_API_KEY=...
  BD_SMS_SENDER_ID=...
  ```
  Adjust the POST field names in `tools/sms.py::_bdgateway_send` to match your
  vendor's contract (they vary).

Map a hospital's own inbound SMS number to a clinic with a `channels` row of
kind `'sms'` (identifier = that inbound number). Unmapped numbers, i.e. the
ASA number, run platform mode as described above. Message bodies are in Bangla
and branded per clinic.

**Billing is per segment.** Bangla SMS use UCS-2 encoding: 70 characters for
a single SMS, 67 per part once it's split. Plain English (GSM-7) fits 160, or
153 per part. A typical Bangla confirmation is 2–3 parts, and each part costs
`CREDIT_COST_SMS`. The Settings → SMS template editor shows the live part
count (`utils/sms_segments.py` and `lib/smsSegments.ts` must stay in sync).

## Delivery reliability (outbox)

Every SMS and WhatsApp send goes through the `outbound_messages` outbox
(`tools/outbox.py`):

- The send is attempted **immediately**, so OTPs still arrive in seconds.
- On a provider failure the message stays `queued` and the backend retries it
  every few minutes with backoff (30 s → 2 min → 10 min → 30 min → 1 h).
  After 6 attempts it becomes `failed`.
- Each kind has a time-to-live. Agent replies expire after 15 min, OTPs after
  10 min, and confirmations/reminders after 24 h. A message past its TTL
  becomes `expired` instead of arriving uselessly late.
- Reminders are idempotent per appointment (`reminder:{appointment_id}`).
- WhatsApp delivery statuses (`delivered` / `read` / `failed`) update the row
  through the existing webhook.
- Inbound retries from Meta (`messages[].id`) and Twilio (`MessageSid`) are
  dropped via `inbound_events`, so a retried webhook never runs a second agent
  turn.

`sms_log` (the console's **Messages** view) records each SMS's final outcome:
sent, failed after retries, or skipped because no provider is configured.

## Checking that it all works

```
make verify
```

This runs a live, read-only check of every integration: the LLM, Postgres,
LiveKit (including whether SIP trunks and dispatch rules exist), SMS,
WhatsApp, payments and Redis. It exits non-zero if a configured integration
is failing. Platform admins can get the same report from
`GET /platform/integrations/verify`.

## Voice calls

Patients can also *call* the WhatsApp number and talk to the voice agent — see [WHATSAPP_CALLING.md](WHATSAPP_CALLING.md).

## Number shows PENDING / "Account not registered"

If WhatsApp suddenly stops working in both directions and sends fail with
`(#133010) Account not registered`, Meta has dropped the number back to status
**PENDING** (happens with Meta test numbers). `make verify` flags it. Fix:

```bash
make whatsapp-register          # registers only if not CONNECTED
make whatsapp-register FORCE=1  # register anyway
```

**Automatic:** the backend does this by itself — it checks the number at
startup and every `WHATSAPP_REGISTRATION_CHECK_MINUTES` (default 30) and
re-registers only if Meta dropped it (log: `WhatsApp number was PENDING —
re-registered`). The command above is the manual version.

It uses `WHATSAPP_REGISTRATION_PIN` (6 digits) from `.env` — this becomes the
number's two-step verification PIN, so keep it; later registrations must use
the same one. Calling settings (docs/WHATSAPP_CALLING.md) survive
re-registration, but the patient may need to reopen the chat to see the call
icon again.

# Voice telephony (inbound phone calls) — Bangladesh

The voice worker (`main.py`) runs the full pipeline locally:
**SIP trunk → LiveKit SIP → Silero VAD → faster-whisper (bn) → LangGraph agent → neural Bangla TTS (MMS)**.

For a real Bangladeshi patient to *call a phone number* and reach the agent, you
need a number (DID) and a SIP trunk that terminates into LiveKit. Twilio does not
offer local BD numbers, so use a **local IP-telephony / SIP provider**.

## The platform number (the default setup)

One number for the whole marketplace. Calls to it run the agent in **platform
mode** — the same cross-hospital experience as the portal home page:

- search doctors across **every** hospital (`search_doctors`),
- answer questions from **any** hospital's uploaded knowledgebase
  (cross-hospital RAG via `search_hospital_info` — each answer names its
  hospital),
- book an appointment at whichever hospital/department the patient picks.

### Who can call: the premium gate

With `VOICE_PREMIUM_GATE=true` (the default), calling the platform number is a
**premium/trial perk** — mirroring the portal, where AI voice is already
premium-only (the voice-token endpoint 402s free-tier patients). The caller is
matched by SIP caller-ID against a phone number they verified **once** via an
SMS OTP on the account page (`/portal/account` → "Book by phone call"):

- **Verified premium/trial caller** → the call joins their unified account
  thread (same context as their portal chat), bookings link to their account,
  and cancel/reschedule work over the phone.
- **Anyone else** (unknown number, unverified, free tier, hidden caller-ID) →
  the agent speaks a polite LLM-composed explanation, an SMS with the
  subscribe/verify link is sent to the caller's number, and the call ends.

One verified number maps to exactly **one** account (DB unique index) — the
same SIM can't verify a fresh account each month to farm new free trials. The
tier is checked at call time, so a lapsed subscription is refused on the very
next call. Known limitation: caller-ID is identification, not authentication —
a spoofed number would be treated as its owner. The only stronger option is a
per-call OTP/PIN, deliberately not implemented (verification is one-time by
design).

Set `VOICE_PREMIUM_GATE=false` to open the number to everyone (and for local
`python main.py console` testing, which has no caller-ID and would otherwise
be declined).

### Setup — nothing to map in the database:

1. Put the provider's details in `.env` (`SIP_INBOUND_NUMBERS`,
   `SIP_INBOUND_ALLOWED_ADDRESSES` and/or auth, plus optionally `SIP_OUTBOUND_*`
   for transfers), then run:
   ```
   python -m scripts.provision_sip --dry-run   # review
   python -m scripts.provision_sip             # apply (idempotent)
   ```
   This creates or updates the `asa-inbound` trunk, the `asa-dispatch`
   **individual** dispatch rule (one room per caller, required for concurrent
   calls) that dispatches the agent, and the optional `asa-outbound` trunk. It
   refuses to create an inbound trunk that accepts calls from anywhere.
2. Point the provider's DID at your LiveKit SIP endpoint.
3. Run the worker: `python main.py start` (production) or `python main.py dev`.
4. Call the number and talk to the platform agent in Bangla.

Why no `channels` row: with an individual dispatch rule the created room is
named after the **caller's** number (plus a random suffix), not the dialed DID
— so the call matches no channel mapping, and `main.py::_resolve_channel`
resolves every unmapped call to platform mode. That fallback is
`VOICE_FALLBACK_SCOPE=platform` (the default); set
`VOICE_FALLBACK_SCOPE=default_clinic` to restore the legacy single-clinic
fallback instead.

Note: with the premium gate off (`VOICE_PREMIUM_GATE=false`), phone callers
are anonymous — they can search, ask, and book, but managing an existing
booking (cancel/reschedule) requires the authenticated patient portal. With
the gate on, verified callers are their account: bookings link to it and
cancel/reschedule work in the call.

## Optional: a dedicated number per hospital or clinic

A hospital that wants its own number keeps the scoped behavior — mapped DIDs
always win over the platform fallback:

```sql
-- Whole hospital (agent greets, asks which department, routes):
INSERT INTO channels (hospital_id, kind, identifier)
VALUES (<hospital_id>, 'voice_ivr', '<dialed-DID, e.g. +8809...>');

-- Single clinic/department:
INSERT INTO channels (clinic_id, kind, identifier)
VALUES (<clinic_id>, 'voice_sip', '<dialed-DID>');
```

`main.py::_resolve_channel` looks the DID up (`voice_ivr` first, then
`voice_sip`/`voice`) and scopes the whole call to that hospital/clinic — its
schedule, branding, bookings.

The dialed number is read from the SIP participant's `sip.trunkPhoneNumber`
attribute. The worker connects and waits up to 8 s for the caller's SIP leg.
The number is tried in its `+880…`, `880…` and `0…` forms, so the `channels`
row may store any of them. Per-hospital numbers therefore work with the same
individual (per-caller) dispatch rule as the platform number. The room name
is only a fallback, for per-DID dispatch rules.

## Live transfer to a person

When a phone caller asks for a person (or the agent can't help them), the
agent calls `request_human_help`. On a **real phone call**, if the hospital
has a **reception phone** set (Hospitals → Edit; a mobile or landline in any
format), the agent says one short "connecting you now" sentence. Once it
finishes speaking, the call is SIP-transferred (REFER) to that number. The
escalation is still recorded.

Browser voice and chat can't be transferred, so they only flag the
conversation for staff, as before. The IPTSP must support SIP REFER on the
trunk.

## What you need to provide

1. **A SIP trunk + DID (phone number)** from a BD provider (IPTSP licensed). Get:
   - SIP server host/port, username/password (or IP allowlist)
   - One or more DIDs (the numbers patients dial)
2. **A running LiveKit server with the SIP service enabled** (self-hosted is fine
   and keeps everything local). Set `LIVEKIT_URL` / `LIVEKIT_API_KEY` /
   `LIVEKIT_API_SECRET` in `.env`.
3. **Regulatory**: confirm BTRC rules for routing inbound PSTN calls to a SIP/VoIP
   endpoint before going live.

## Notes / current status

- Latency: faster-whisper `large-v3` and MMS-TTS are CPU-capable but a GPU makes
  voice real-time. See the production plan (central GPU host).
- `console` mode (`python main.py console`) needs no SIP — it uses your laptop
  mic/speakers and is the quickest way to test the pipeline end to end.
- The LiveKit SIP integration details (trunk/dispatch API) depend on the
  livekit-agents version; verify against your installed version before launch.

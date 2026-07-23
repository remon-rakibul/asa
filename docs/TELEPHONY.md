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

1. Self-host LiveKit + the SIP service (or use LiveKit Cloud SIP). Create an
   **inbound trunk** pointing at your provider, and an **individual dispatch
   rule** (one room per caller — required for concurrent calls) that dispatches
   the `appointment-setter` agent (`RoomAgentDispatch` with that
   `agent_name`).
2. Point the provider's DID at the LiveKit SIP trunk.
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

**Caveat:** this lookup matches the DID against the **room name**, so a
dedicated number needs a dispatch rule that puts the dialed number in the room
name (e.g. a per-DID rule with a fixed room prefix). Individual per-caller
rules name rooms after the caller and will fall through to the platform
fallback instead. (Resolving the dialed number from LiveKit's SIP participant
attributes — `sip.trunkPhoneNumber` — would remove this constraint; not
implemented yet.)

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

# Voice telephony (inbound phone calls) — Bangladesh

The voice worker (`main.py`) runs the full pipeline locally:
**SIP trunk → LiveKit SIP → Silero VAD → faster-whisper (bn) → LangGraph agent → neural Bangla TTS (MMS)**.

For a real Bangladeshi patient to *call a phone number* and reach the agent, you
need a number (DID) and a SIP trunk that terminates into LiveKit. Twilio does not
offer local BD numbers, so use a **local IP-telephony / SIP provider**.

## What you need to provide
1. **A SIP trunk + DID (phone number)** from a BD provider (IPTSP licensed). Get:
   - SIP server host/port, username/password (or IP allowlist)
   - One or more DIDs (the numbers patients dial)
2. **A running LiveKit server with the SIP service enabled** (self-hosted is fine
   and keeps everything local). Set `LIVEKIT_URL` / `LIVEKIT_API_KEY` /
   `LIVEKIT_API_SECRET` in `.env`.
3. **Regulatory**: confirm BTRC rules for routing inbound PSTN calls to a SIP/VoIP
   endpoint before going live.

## Wiring a DID to a clinic (multi-tenant)
Each dialed number maps to a clinic via the `voice_sip` channel:

```sql
INSERT INTO channels (clinic_id, kind, identifier)
VALUES (<clinic_id>, 'voice_sip', '<dialed-DID, e.g. +8809...>');
```

`main.py::_resolve_clinic_id` looks the DID up and scopes the whole call to that
clinic (its schedule, branding, bookings). Unmapped calls fall back to the
default clinic.

## Steps
1. Self-host LiveKit + the SIP service (see LiveKit's SIP docs). Create an
   inbound trunk pointing at your provider, and a dispatch rule that routes calls
   to the `appointment-setter` agent.
2. Point the provider's DID at the LiveKit SIP trunk.
3. Run the worker: `python main.py start` (production) or `python main.py dev`.
4. Register each DID in the `channels` table (above).
5. Call the number and book an appointment in Bangla.

## Notes / current status
- Latency: faster-whisper `large-v3` and MMS-TTS are CPU-capable but a GPU makes
  voice real-time. See the production plan (central GPU host).
- `console` mode (`python main.py console`) needs no SIP — it uses your laptop
  mic/speakers and is the quickest way to test the pipeline end to end.
- The LiveKit SIP integration details (trunk/dispatch API) depend on the
  livekit-agents version; verify against your installed version before launch.

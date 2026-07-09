# Browser voice calls (patient portal)

Patients can talk to the booking agent from the portal UI — no phone/SIP needed.
The browser joins a LiveKit room and the voice worker is dispatched into it.

This is the same agent as `python main.py console`, just connected to a LiveKit
server instead of the local mic.

## How it works

1. The patient presses a voice button in the portal:
   - **Booking page** (`/portal/book`) → department-level call (sends `clinic_id`).
   - **Home page** (`/portal`) → a hospital-level "talk to us" call (sends
     `hospital_id`; the agent asks which department), or a department-level call
     once a department is chosen.
2. The browser calls **`POST /patient/voice/token`** (`api/routes/voice.py`). The
   server authenticates the patient, derives clinic/hospital + patient identity,
   and mints a LiveKit `AccessToken` whose `RoomConfiguration` carries a
   `RoomAgentDispatch` for the `appointment-setter` agent (with that identity as
   JSON metadata). It returns `{server_url, participant_token, room_name}`.
3. The browser (`components/portal/VoiceCall.tsx`) connects with the LiveKit React
   components. On room creation the worker is dispatched; `main._resolve_channel`
   reads `ctx.job.metadata` to scope the call and link the booking to the account.

## In-call UX

The voice modal (`components/portal/VoiceCall.tsx`) shows:

- **Live captions** — a scrolling transcript of both sides (`useTranscriptions()`
  over LiveKit's `lk.transcription` text stream), so the patient can read names,
  the chosen slot, and the **serial number**.
- **Mic-permission view** — a dedicated prompt when the browser blocks the mic
  (`onMediaDeviceFailure` → `MediaDeviceFailure.PermissionDenied`/`NotFound`).
- **Post-call confirmation card** — when a booking completes, the worker publishes a
  `booking` data packet (`voice/langgraph_llm.py::_publish_booking`) with the
  appointment id / serial / slot; the browser renders a confirmation card (and keeps
  the modal open after the call ends to show it). Telephony has no room, so this is a
  no-op there.

## Turn-taking & audio quality

For streaming STT (LiveKit Inference), `main.py` configures the session with the
**LiveKit Inference turn-detector model** (`inference.TurnDetector()`) for natural
Bangla end-of-turn, plus interruption tuning (`min_interruption_duration`,
`resume_false_interruption`, widened endpointing delays) so short back-channels
("হ্যাঁ", "আচ্ছা") don't cut the agent off while real barge-in still works. The
turn detector runs on LiveKit Inference at no extra cost.

**Optional noise cancellation:** set `VOICE_NOISE_CANCELLATION=true` to apply LiveKit
voice-isolation to the call input (clearer STT in noisy clinics). This needs the
`livekit-plugins-noise-cancellation` package and is a LiveKit Cloud feature with
possible usage cost, so it is **off by default**; if the flag is on but the plugin is
missing, the worker logs a warning and continues without it
(`main.py::_room_input_options`).

## Running it (dev)

Uses the existing **LiveKit Cloud** project — already configured in `.env`
(`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`). STT/TTS run via LiveKit
Inference (`STT_ENGINE=livekit`, `TTS_ENGINE=livekit`).

1. **API** — `livekit-api` is in `requirements.txt`; just run the FastAPI app as usual.
2. **Voice worker** — must run in `dev` (or `start`) mode, **not** `console`, so it
   connects to the server and accepts dispatched rooms:
   ```bash
   .venv/bin/python main.py dev
   ```
   (Install the voice deps first: `pip install -r requirements-voice.txt`.)
3. **UI** — `npm run dev`. Log in as a patient, pick a hospital → department, and
   press the voice button.

> The token endpoint and the worker must share the same `LIVEKIT_API_KEY` /
> `LIVEKIT_API_SECRET` as the LiveKit server.

## Switching STT/TTS later

Moving off LiveKit Inference is config-only — set `STT_ENGINE` / `TTS_ENGINE` in
`.env`:

- `STT_ENGINE=whisper` — local faster-whisper (no audio leaves the box).
- `TTS_ENGINE=mms` — local neural Bangla (facebook/mms-tts-ben), or `gemini` for
  Google cloud TTS.

See `voice/stt_factory.py` / `voice/tts_factory.py` for all options. (Self-hosting
the open-source LiveKit server is also possible — point `LIVEKIT_URL` at it and use
its API key/secret — but Inference STT/TTS is a Cloud feature, so use local engines
there.)

## Telephony unchanged

Inbound SIP/DID calls (`docs/TELEPHONY.md`) are untouched: metadata resolution is
additive and only triggers for browser rooms that carry dispatch metadata. Phone
rooms still resolve the dialed number via `ctx.room.name`.

"""Wrap the LangGraph appointment agent as a LiveKit `llm.LLM`.

LiveKit's voice pipeline requires a real `llm.LLM` object. This adapter ignores
the accumulated ChatContext except for the *latest user message* and drives our
stateful graph (which keeps its own per-call state via the Postgres checkpointer,
keyed by session_id). That matches our one-message-per-turn design.
"""

from __future__ import annotations

import json
import logging
import uuid

from livekit.agents import llm

from agent.runner import stream_turn_tokens

log = logging.getLogger(__name__)


def _latest_user_text(chat_ctx: llm.ChatContext) -> str:
    items = getattr(chat_ctx, "items", None) or getattr(chat_ctx, "messages", [])
    for item in reversed(items):
        if getattr(item, "role", None) != "user":
            continue
        text = getattr(item, "text_content", None)
        if callable(text):
            text = text()
        if text:
            return text
        content = getattr(item, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, (list, tuple)):
            return " ".join(c for c in content if isinstance(c, str))
    return ""


class LangGraphLLM(llm.LLM):
    def __init__(
        self,
        graph,
        session_id: str,
        clinic_id: int | None = None,
        channel_identifier: str | None = None,
        *,
        hospital_id: int | None = None,
        doctor_id: int | None = None,
        departments: list | None = None,
        patient: dict | None = None,
        room=None,
        platform: bool = False,
        booked_appointment_id: str | None = None,
    ):
        super().__init__()
        self._graph = graph
        self._session_id = session_id
        self._clinic_id = clinic_id
        self._channel_identifier = channel_identifier
        self._hospital_id = hospital_id
        # Platform-wide assistant (marketplace home): the agent searches
        # doctors across all hospitals; no clinic/hospital scope at call start.
        self._platform = platform
        # Doctor pre-selected in the portal wizard before the call started
        # (browser dispatch metadata). None for telephony.
        self._doctor_id = doctor_id
        self._departments = departments
        # LiveKit room — lets a completed voice booking push a data packet to the
        # browser so it can render a confirmation card. None for telephony.
        self._room = room
        # Browser-portal patient identity from the dispatch metadata (account_id,
        # patient_id, name, phone). None for telephony. Lets the spoken booking
        # link to the patient account, like the text path.
        self._patient = patient or {}
        # Set by DoctorAssistant.on_enter() when a returning patient is identified
        # via SIP caller-ID lookup. Injected into the first graph turn as state.
        self.known_patient: dict | None = None
        # Last appointment_id pushed to the browser — every end event after a
        # booking carries the id, so publish the card only once per booking.
        # Seeded with any booking already in this thread's checkpoint (a prior
        # chat/call on the unified thread) so a stale serial is NOT re-published
        # when the call connects; only a NEW in-call booking pushes the banner.
        self.published_appointment_id: str | None = booked_appointment_id

    def chat(self, *, chat_ctx, tools=None, conn_options=None, **kwargs):
        return _LangGraphStream(
            self,
            graph=self._graph,
            session_id=self._session_id,
            clinic_id=self._clinic_id,
            channel_identifier=self._channel_identifier,
            hospital_id=self._hospital_id,
            doctor_id=self._doctor_id,
            departments=self._departments,
            known_patient=self.known_patient,
            patient=self._patient,
            room=self._room,
            platform=self._platform,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )


class _LangGraphStream(llm.LLMStream):
    def __init__(self, llm_, *, graph, session_id, clinic_id, channel_identifier,
                 hospital_id, doctor_id, departments, known_patient, patient, room,
                 chat_ctx, tools, conn_options, platform=False):
        super().__init__(llm_, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._graph = graph
        self._session_id = session_id
        self._clinic_id = clinic_id
        self._channel_identifier = channel_identifier
        self._hospital_id = hospital_id
        self._platform = platform
        self._doctor_id = doctor_id
        self._departments = departments
        self._known_patient = known_patient
        self._patient = patient or {}
        self._room = room

    async def _run(self) -> None:
        user_text = _latest_user_text(self._chat_ctx)
        # Stream the spoken reply token-by-token so the TTS layer can start
        # speaking the first clause while the rest is still being generated.
        # LiveKit's StreamAdapter re-segments these deltas into sentences for TTS.
        kp = self._known_patient or {}
        wp = self._patient or {}
        agen = stream_turn_tokens(
            self._graph, self._session_id, user_text, self._clinic_id, channel="voice",
            channel_identifier=self._channel_identifier,
            hospital_id=self._hospital_id,
            doctor_id=self._doctor_id,
            departments=self._departments,
            patient_name=wp.get("name") or kp.get("name"),
            patient_age=kp.get("age"),
            patient_mobile=wp.get("phone"),
            patient_id=wp.get("patient_id") or kp.get("id"),
            patient_account_id=wp.get("account_id"),
            platform=self._platform,
        )
        try:
            async for event in agen:
                etype = event.get("type")
                if etype == "end":
                    # A booking just completed → tell the browser so it can show a
                    # confirmation card. No-op for telephony (no room).
                    if event.get("appointment_id"):
                        await self._publish_booking(event)
                    continue
                if etype == "confirm":
                    # A cancel/reschedule confirm question — speak it. The next
                    # utterance is interpreted as yes/no by the runner (it
                    # detects the interrupted thread and resumes the graph).
                    question = event.get("question") or ""
                    if question:
                        self._event_ch.send_nowait(
                            llm.ChatChunk(
                                id=str(uuid.uuid4()),
                                delta=llm.ChoiceDelta(role="assistant", content=question),
                            )
                        )
                    continue
                if etype != "token":
                    continue
                text = event.get("text") or ""
                if text:
                    self._event_ch.send_nowait(
                        llm.ChatChunk(
                            id=str(uuid.uuid4()),
                            delta=llm.ChoiceDelta(role="assistant", content=text),
                        )
                    )
        finally:
            # If LiveKit cancels this turn mid-generation (caller interrupts or
            # hangs up), close the graph stream so LangGraph's internal queue
            # tasks are cancelled too — otherwise they leak as
            # "Task was destroyed but it is pending!". `except Exception` lets a
            # genuine CancelledError (BaseException) keep propagating.
            try:
                await agen.aclose()
            except Exception:
                pass

    async def _publish_booking(self, event: dict) -> None:
        """Push the booking result to the browser on the 'booking' data topic.

        Once per booking: state keeps appointment_id after the booking turn, so
        every later end event in the call would otherwise re-send the card.
        """
        if self._room is None:
            return
        llm_obj = getattr(self, "_llm", None)
        apt_id = event.get("appointment_id")
        if llm_obj is not None:
            if getattr(llm_obj, "published_appointment_id", None) == apt_id:
                return
            llm_obj.published_appointment_id = apt_id
        try:
            payload = json.dumps({
                "appointment_id": event.get("appointment_id"),
                "serial_number": event.get("serial_number"),
                "slot_label": event.get("slot_label"),
            }).encode()
            await self._room.local_participant.publish_data(
                payload, reliable=True, topic="booking",
            )
        except Exception as exc:  # best-effort — never break the spoken turn
            log.debug("booking data publish failed: %s", exc)

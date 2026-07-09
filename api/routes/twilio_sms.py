"""Twilio SMS webhook — receives inbound patient texts and replies via the agent.

Twilio POST fields (form-encoded):
  From  – the patient's phone number, e.g. +8801712345678
  Body  – the message text

The patient's phone number doubles as the LangGraph thread_id so each number
gets its own persistent conversation state.

Webhook URL to configure in Twilio console:
  https://<your-host>/twilio/sms   (HTTP POST)

Twilio signature validation is enabled when TWILIO_AUTH_TOKEN is set.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, Response

from agent.runner import run_turn
from config import settings
from tools.reminders import handle_reminder_reply
from tools.sms import send_sms

from ..deps import resolve_channel_clinic

log = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio", tags=["twilio"])


def _validate_signature(request: Request, form_data: dict) -> None:
    """Reject requests that don't carry a valid Twilio signature."""
    if not settings.twilio_auth_token:
        return  # validation disabled — set TWILIO_AUTH_TOKEN to enable

    from twilio.request_validator import RequestValidator

    validator = RequestValidator(settings.twilio_auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    if not validator.validate(url, form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


async def _handle_sms_turn(
    graph, session_id: str, body_text: str, clinic_id: int, reply_to: str
) -> None:
    """Run the agent turn for an inbound SMS and deliver the reply.

    Executed as a background task so the webhook can return immediately (avoiding
    Twilio's 15s timeout + retry double-run). The agent's reply is sent back to
    the patient out-of-band via the SMS provider.
    """
    try:
        # Two-way reminder: a plain ১/২ reply confirms or cancels the reminded
        # appointment deterministically — no LLM turn.
        reminder_reply = await handle_reminder_reply(reply_to, body_text)
        if reminder_reply:
            await send_sms(reply_to, reminder_reply, clinic_id=clinic_id)
            return
        result = await run_turn(graph, session_id, body_text, clinic_id)
        reply = (result or {}).get("reply")
        if reply:
            await send_sms(reply_to, reply, clinic_id=clinic_id)
    except Exception:
        log.exception("Twilio SMS background turn failed for session %s", session_id)


@router.post("/sms")
async def sms_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
    To: str = Form(""),
) -> Response:
    """Handle an inbound Twilio SMS.

    Returns an empty TwiML response immediately and processes the agent turn in
    the background. This prevents Twilio's 15-second webhook timeout from
    triggering retries that would run the agent turn twice.
    Note: outbound SMS is sent via the SMS provider (not TwiML), so the empty
    response body is correct.
    """
    form_data = dict(await request.form())
    _validate_signature(request, form_data)

    # The clinic's own inbound number (To) maps to its clinic; the patient's
    # number (From) is the conversation thread id.
    clinic_id = await resolve_channel_clinic("sms", To)
    session_id = f"sms-{From}"
    background_tasks.add_task(
        _handle_sms_turn, request.app.state.graph, session_id, Body, clinic_id, From
    )

    # Empty TwiML — the reply is sent asynchronously via the SMS provider.
    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=twiml, media_type="application/xml")

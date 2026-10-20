"""Twilio SMS webhook — receives inbound patient texts and replies via the agent.

Twilio POST fields (form-encoded):
  From  – the patient's phone number, e.g. +8801712345678
  Body  – the message text

Routing (api/messaging.py): a hospital number mapped in `channels` keeps its
clinic; the ASA number runs platform mode. A verified sender joins their
account thread; anyone else gets a thread keyed on their number.

Webhook URL to configure in Twilio console:
  https://<your-host>/twilio/sms   (HTTP POST)

Twilio signature validation is enabled when TWILIO_AUTH_TOKEN is set.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, Response

from agent.runner import run_message_turn
from config import settings
from tools.outbox import seen_inbound
from tools.reminders import handle_reminder_reply
from tools.sms import send_sms

from ..messaging import resolve_message_scope, sender_lock

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


async def _handle_sms_turn(graph, body_text: str, business_number: str, reply_to: str) -> None:
    """Route and run the agent turn for an inbound SMS, then deliver the reply.

    Executed as a background task so the webhook can return immediately (avoiding
    Twilio's 15s timeout + retry double-run). The agent's reply is sent back to
    the patient out-of-band via the SMS provider.
    """
    try:
        # Two-way reminder: a plain ১/২ reply confirms or cancels the reminded
        # appointment deterministically — no LLM turn.
        reminder_reply = await handle_reminder_reply(reply_to, body_text)
        if reminder_reply:
            await send_sms(reply_to, reminder_reply)
            return
        scope = await resolve_message_scope("sms", business_number, reply_to)
        async with sender_lock(scope["session_id"]):
            result = await run_message_turn(
                graph, scope["session_id"], body_text,
                channel_identifier=reply_to, **scope["turn"],
            )
        if result["reply"]:
            await send_sms(reply_to, result["reply"], clinic_id=result["clinic_id"])
    except Exception:
        log.exception("Twilio SMS background turn failed for %s", reply_to)


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

    # Twilio retries a webhook it thinks failed; MessageSid identifies the
    # message, so a retry never runs a second agent turn / duplicate reply.
    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    if await seen_inbound("twilio", str(form_data.get("MessageSid", ""))):
        return Response(content=twiml, media_type="application/xml")

    # A hospital's own number (To) mapped in `channels` keeps its clinic;
    # the ASA number runs platform mode (api/messaging.py).
    background_tasks.add_task(_handle_sms_turn, request.app.state.graph, Body, To, From)

    # Empty TwiML — the reply is sent asynchronously via the SMS provider.
    return Response(content=twiml, media_type="application/xml")

"""Outbox (tools/outbox.py), provider number formats, and webhook de-dup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import tools.outbox as outbox


class FakeConn:
    """Records SQL; returns queued answers for fetchrow/fetchval."""

    def __init__(self, fetchrow=None, fetchval=None, raise_on_fetchrow=False):
        self.sql: list[str] = []
        self._fetchrow, self._fetchval = fetchrow, fetchval
        self._raise = raise_on_fetchrow

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if self._raise:
            raise ConnectionError("db down")
        return self._fetchrow

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return self._fetchval

    async def execute(self, sql, *args):
        self.sql.append(sql)
        return "UPDATE 1"

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return []


def _pool(conn: FakeConn):
    pool = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return AsyncMock(return_value=pool)


def _row(attempts=1, channel="sms", kind="confirmation"):
    from tools.crypto import encrypt_field
    return {"id": 7, "channel": channel, "kind": kind, "to_number": encrypt_field("01712345678"),
            "body": encrypt_field("হ্যালো"), "sender_id": None, "clinic_id": 3, "attempts": attempts}


@pytest.fixture
def bookkeeping(monkeypatch):
    sent, failed = AsyncMock(), AsyncMock()
    monkeypatch.setattr(outbox, "_on_sent", sent)
    monkeypatch.setattr(outbox, "_on_failed", failed)
    return sent, failed


@pytest.mark.asyncio
async def test_unconfigured_channel_is_skipped_not_queued(monkeypatch):
    log_sms = AsyncMock()
    with patch("tools.database.log_sms", log_sms), patch("tools.database.get_pool") as gp:
        status = await outbox.send("sms", "017", "x", kind="otp", configured=False)
    assert status == "skipped"
    gp.assert_not_called()          # nothing queued → no endless retries
    assert log_sms.await_args.kwargs["status"] == "skipped"


@pytest.mark.asyncio
async def test_inline_success_marks_sent_and_meters(monkeypatch, bookkeeping):
    sent, failed = bookkeeping
    conn = FakeConn(fetchrow=_row())
    monkeypatch.setattr(outbox, "_sender",
                        lambda ch: AsyncMock(return_value=("twilio", "SM123")))
    with patch("tools.database.get_pool", _pool(conn)):
        status = await outbox.send("sms", "01712345678", "হ্যালো", kind="confirmation", clinic_id=3)
    assert status == "sent"
    assert any("status='sent'" in q for q in conn.sql)
    sent.assert_awaited_once()
    assert sent.await_args.args[:3] == ("sms", "01712345678", "হ্যালো")   # decrypted
    failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_requeues_with_backoff(monkeypatch, bookkeeping):
    sent, failed = bookkeeping
    conn = FakeConn(fetchrow=_row(attempts=1), fetchval="queued")
    monkeypatch.setattr(outbox, "_sender",
                        lambda ch: AsyncMock(side_effect=RuntimeError("gateway 503")))
    with patch("tools.database.get_pool", _pool(conn)):
        status = await outbox.send("sms", "01712345678", "x", kind="confirmation")
    assert status == "queued"
    assert any("next_attempt_at" in q for q in conn.sql)
    sent.assert_not_awaited()
    failed.assert_not_awaited()     # not terminal yet → not logged as failed


@pytest.mark.asyncio
async def test_last_attempt_failure_is_terminal(monkeypatch, bookkeeping):
    _, failed = bookkeeping
    conn = FakeConn()
    monkeypatch.setattr(outbox, "_sender",
                        lambda ch: AsyncMock(side_effect=RuntimeError("bad number")))
    with patch("tools.database.get_pool", _pool(conn)):
        status = await outbox._attempt(_row(attempts=outbox.MAX_ATTEMPTS))
    assert status == "failed"
    failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_is_not_resent(monkeypatch, bookkeeping):
    sender = AsyncMock()
    monkeypatch.setattr(outbox, "_sender", lambda ch: sender)
    with patch("tools.database.get_pool", _pool(FakeConn(fetchrow=None))):
        status = await outbox.send("sms", "017", "x", kind="reminder", idempotency_key="reminder:a1")
    assert status == "duplicate"
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_down_falls_back_to_direct_send(monkeypatch, bookkeeping):
    sent, _ = bookkeeping
    monkeypatch.setattr(outbox, "_sender", lambda ch: AsyncMock(return_value=("twilio", "SM1")))
    with patch("tools.database.get_pool", _pool(FakeConn(raise_on_fetchrow=True))):
        status = await outbox.send("sms", "017", "x", kind="otp")
    assert status == "sent"         # the outbox must never lose a message
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_seen_inbound():
    with patch("tools.database.get_pool", _pool(FakeConn(fetchval=1))):
        assert await outbox.seen_inbound("whatsapp", "wamid.A") is False   # first time
    with patch("tools.database.get_pool", _pool(FakeConn(fetchval=None))):
        assert await outbox.seen_inbound("whatsapp", "wamid.A") is True    # retry
    assert await outbox.seen_inbound("whatsapp", "") is False
    with patch("tools.database.get_pool", AsyncMock(side_effect=ConnectionError)):
        assert await outbox.seen_inbound("whatsapp", "wamid.B") is False   # fail open


def test_provider_number_formats():
    from tools.whatsapp import _wa_number

    assert _wa_number("01712345678") == "8801712345678"
    assert _wa_number("8801712345678") == "8801712345678"     # Meta inbound `from`
    with patch("tools.sms._twilio_client") as client:
        client.return_value.messages.create.return_value.sid = "SM9"
        from tools.sms import _twilio_send
        assert _twilio_send("01712345678", "x", "+1555") == "SM9"
        assert client.return_value.messages.create.call_args.kwargs["to"] == "+8801712345678"


# --------------------------------------------------------------------------- #
# WhatsApp webhook: batches, retries, delivery statuses
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    from api.app import app

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("api.app.send_pending_reminders", new_callable=AsyncMock, return_value=0),
        patch("api.app._outbox_loop", new_callable=AsyncMock),
    ):
        with TestClient(app) as c:
            yield c


def _msg(mid, text):
    return {"id": mid, "from": "8801711000000", "type": "text", "text": {"body": text}}


def test_whatsapp_batch_handles_every_message_once(client):
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"display_phone_number": "8809600000000"},
        "messages": [_msg("wamid.1", "হ্যালো"), _msg("wamid.2", "ডাক্তার")],
        "statuses": [{"id": "wamid.out", "status": "delivered"}],
    }}]}]}
    seen = {"wamid.2"}   # wamid.2 is a Meta retry of an already-handled message
    turn, delivery = AsyncMock(), AsyncMock(return_value=True)
    with (
        patch("api.routes.whatsapp.seen_inbound", AsyncMock(side_effect=lambda p, i: i in seen)),
        patch("api.routes.whatsapp._handle_whatsapp_turn", turn),
        patch("api.routes.whatsapp.mark_delivery", delivery),
        patch("api.routes.whatsapp._verify_meta_signature", lambda *a: None),
    ):
        r = client.post("/whatsapp/webhook", json=payload)
    assert r.json() == {"status": "ok"}
    assert turn.await_count == 1 and turn.await_args.args[1] == "হ্যালো"
    delivery.assert_awaited_once_with("whatsapp", "wamid.out", delivered=True, error=None)


def test_twilio_retry_is_ignored(client):
    with (
        patch("api.routes.twilio_sms._validate_signature", new=lambda r, f: None),
        patch("api.routes.twilio_sms.seen_inbound", AsyncMock(return_value=True)),
        patch("api.routes.twilio_sms.run_message_turn", new_callable=AsyncMock) as run,
    ):
        r = client.post("/twilio/sms", content="From=%2B8801711000000&Body=hi&MessageSid=SM1",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "<Response></Response>" in r.text
    run.assert_not_awaited()


# --------------------------------------------------------------------------- #
# WhatsApp voice notes
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("engine,audio,stt,expected", [
    ("off", b"x", None, "UNREADABLE"),                         # no engine at all
    ("gemini", None, None, "UNREADABLE"),                      # media download failed
    ("gemini", b"ogg", RuntimeError("quota"), "UNREADABLE"),   # STT call failed
    ("gemini", b"ogg", "", "UNREADABLE"),                      # silence
    ("gemini", b"ogg", "ডাক্তার দেখাতে চাই", "ডাক্তার দেখাতে চাই"),
])
async def test_voice_note_never_silently_dropped(monkeypatch, engine, audio, stt, expected):
    import tools.whatsapp as wa

    monkeypatch.setattr(wa, "_voice_note_engine", lambda: engine)
    monkeypatch.setattr(wa, "download_media", AsyncMock(return_value=audio))
    if isinstance(stt, Exception):
        monkeypatch.setattr(wa, "_gemini_transcribe", AsyncMock(side_effect=stt))
    else:
        monkeypatch.setattr(wa, "_gemini_transcribe", AsyncMock(return_value=stt))
    out = await wa.transcribe_voice_note("media-1", "audio/ogg; codecs=opus")
    # An unreadable note becomes an agent instruction, so the LLM composes the
    # "please type it" reply — never silence, never canned patient text.
    assert out == (wa.VOICE_NOTE_UNREADABLE if expected == "UNREADABLE" else expected)


def test_voice_note_engine_auto(monkeypatch):
    import tools.whatsapp as wa
    from config import settings

    monkeypatch.setattr(settings, "whatsapp_voice_note_stt", "auto")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    assert wa._voice_note_engine() == "gemini"
    monkeypatch.setattr(settings, "whatsapp_voice_note_stt", "whisper")
    assert wa._voice_note_engine() == "whisper"     # explicit choice keeps audio local

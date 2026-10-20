"""SMS segment counting (utils/sms_segments.py) and per-segment metering.
Cases mirrored in appointment-ui/tests/smsSegments.test.ts."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from utils.sms_segments import sms_encoding, sms_segments

CASES = [
    ("", 0),
    ("a" * 160, 1),
    ("a" * 161, 2),           # concatenated GSM-7: 153 per part
    ("a" * 306, 2),
    ("a" * 307, 3),
    ("€" * 80, 1),            # extension chars take two septets: 160 units
    ("€" * 81, 2),
    ("আ" * 70, 1),            # Bangla → UCS-2: 70 single
    ("আ" * 71, 2),            # 67 per part once concatenated
    ("আ" * 134, 2),
    ("আ" * 135, 3),
    ("hello আ", 1),           # one Bangla char makes the whole message UCS-2
]


@pytest.mark.parametrize("text,parts", CASES)
def test_segments(text, parts):
    assert sms_segments(text) == parts


def test_bangla_is_ucs2():
    assert sms_encoding("Your serial is 5") == "gsm7"
    assert sms_encoding("সিরিয়াল ৫") == "ucs2"


def test_default_bangla_confirmation_costs_several_parts():
    from tools.sms import DEFAULT_TEMPLATES
    assert sms_segments(DEFAULT_TEMPLATES["confirmation"]) >= 2


@pytest.mark.asyncio
async def test_sent_sms_is_metered_per_segment(monkeypatch):
    import tools.outbox as outbox
    from config import settings

    monkeypatch.setattr(settings, "credit_cost_sms", 1)
    charge = AsyncMock()
    with patch("tools.database.charge_channel_usage", charge), patch("tools.database.log_sms", AsyncMock()):
        await outbox._on_sent("sms", "017", "আ" * 135, "reminder", 3, "twilio")
    assert charge.await_args.kwargs["credits"] == 3
    assert charge.await_args.kwargs["quantity"] == 3

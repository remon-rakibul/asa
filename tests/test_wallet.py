"""Hospital credit-wallet DB-layer guards.

The transactional draw-down/idempotency behaviour is verified live against
Postgres (see the migration/functional check); these tests pin the pure guard
logic that must hold with no database at all: the feature flag makes every
metering call an inert no-op, and missing hospital/clinic ids are skipped. That
guarantee is what lets the hooks be sprinkled through the booking/SMS/voice
paths without risk when credits are disabled (the default).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import tools.database as db


def _no_pool():
    """Any DB access would explode — proves the call short-circuited."""
    return patch("tools.database.get_pool",
                 new=AsyncMock(side_effect=AssertionError("DB touched")))


@pytest.fixture
def credits_off():
    with patch.object(db.settings, "credits_enabled", False):
        yield


@pytest.fixture
def credits_on():
    with patch.object(db.settings, "credits_enabled", True):
        yield


async def test_charge_wallet_noop_when_disabled(credits_off):
    with _no_pool():
        assert await db.charge_wallet(10, reason="booking", credits=5) is None


async def test_charge_wallet_noop_when_no_hospital(credits_on):
    with _no_pool():
        assert await db.charge_wallet(None, reason="booking", credits=5) is None


async def test_charge_wallet_noop_when_zero_credits(credits_on):
    with _no_pool():
        assert await db.charge_wallet(10, reason="booking", credits=0) is None


async def test_channel_usage_noop_when_disabled(credits_off):
    with _no_pool():
        assert await db.charge_channel_usage(2, reason="sms", credits=1) is None


async def test_channel_usage_noop_when_no_clinic(credits_on):
    with _no_pool():
        assert await db.charge_channel_usage(None, reason="sms", credits=1) is None


async def test_channel_usage_standalone_clinic_not_billed(credits_on):
    """A clinic with no parent hospital resolves to None → nothing charged."""
    with (
        patch("tools.database.get_hospital_id_for_clinic", new=AsyncMock(return_value=None)),
        patch("tools.database.charge_wallet", new=AsyncMock()) as charge,
    ):
        await db.charge_channel_usage(2, reason="sms", credits=1)
    charge.assert_not_awaited()


async def test_booking_credit_noop_when_disabled(credits_off):
    with _no_pool():
        await db._charge_booking_credit("apt-1", 2)  # must not raise / touch DB


async def test_booking_credit_charges_resolved_hospital(credits_on):
    with (
        patch("tools.database.get_hospital_id_for_clinic", new=AsyncMock(return_value=7)),
        patch("tools.database.charge_wallet", new=AsyncMock()) as charge,
    ):
        await db._charge_booking_credit("apt-9", 2)
    charge.assert_awaited_once()
    kw = charge.await_args.kwargs
    assert kw["reason"] == "booking"
    assert kw["idempotency_key"] == "booking:apt-9"
    assert kw["credits"] == db.settings.credit_cost_booking


async def test_booking_credit_failopen_on_error(credits_on):
    """A metering failure must never propagate out of the booking path."""
    with patch("tools.database.get_hospital_id_for_clinic",
               new=AsyncMock(side_effect=RuntimeError("db down"))):
        await db._charge_booking_credit("apt-x", 2)  # swallowed


async def test_sweep_wallet_debt_noop_when_disabled(credits_off):
    with _no_pool():
        assert await db.sweep_wallet_debt() == 0

"""Patient-memory context: a returning patient is still recognised (no re-asking
name/age), but the greeting must be FRESH — no "welcome back / আবারও" — so a
deliberately cleared chat (the "New" button) doesn't feel like it remembers the
past conversation. The identity/visit memory itself is intentionally preserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent.nodes import _load_patient_context


class _FakeItem:
    def __init__(self, key, value, created_at):
        self.key = key
        self.value = value
        self.created_at = created_at


class _FakeStore:
    """Minimal LangGraph BaseStore stand-in: profile + visit items."""

    def __init__(self, profile, visits):
        self._profile = profile
        self._visits = visits  # list[_FakeItem]

    async def aget(self, _namespace, key):
        if key == "profile" and self._profile is not None:
            return SimpleNamespace(value=self._profile)
        return None

    async def asearch(self, _namespace, *, query=None, limit=20):
        return list(self._visits)[:limit]


def _visit(key, summary, when):
    return _FakeItem(key, {"summary": summary}, when)


@pytest.mark.asyncio
async def test_greeting_turn_is_fresh_and_omits_past_visits():
    """Greeting turn = empty patient message (query is None). The patient is
    still recognised by name (no re-asking), the greeting is FRESH (no
    welcome-back), and — critically — NO past-visit doctor leaks into the
    opening line of a fresh chat where no doctor was selected."""
    now = datetime.now(timezone.utc)
    store = _FakeStore(
        profile={"name": "রকিবুল", "age": 30, "phone": "01700000000"},
        visits=[_visit("visit:1", "কার্ডিওলজি — ডা. স্মিথ", now)],
    )
    context, last_visit = await _load_patient_context(17, query=None, store=store)

    # Still recognised (so the agent doesn't re-ask identity); last_visit still
    # computed for callers that need it.
    assert "RETURNING PATIENT" in context
    assert "রকিবুল" in context
    assert last_visit is not None

    # Greeting is FRESH — welcome-back phrasings appear ONLY in the DON'T list.
    assert "starting a NEW conversation" in context
    assert "do NOT say" in context and "আবারও" in context
    assert "warmly" not in context

    # The past-visit doctor must NOT be in the greeting context at all.
    assert "স্মিথ" not in context
    assert "Previous visits" not in context


@pytest.mark.asyncio
async def test_past_visits_available_once_patient_speaks():
    """When the patient actually says something (query present), the past visit
    IS surfaced — as reference-only — so 'গতবারের ডাক্তার' recall keeps working."""
    now = datetime.now(timezone.utc)
    store = _FakeStore(
        profile={"name": "রকিবুল", "age": 30, "phone": "01700000000"},
        visits=[_visit("visit:1", "কার্ডিওলজি — ডা. স্মিথ", now)],
    )
    context, _ = await _load_patient_context(17, query="গতবারের ডাক্তার", store=store)
    assert "Previous visits" in context
    assert "স্মিথ" in context
    assert "do NOT mention these unless" in context


@pytest.mark.asyncio
async def test_no_profile_returns_empty_context():
    store = _FakeStore(profile=None, visits=[])
    context, last_visit = await _load_patient_context(17, query=None, store=store)
    assert context == "" and last_visit is None

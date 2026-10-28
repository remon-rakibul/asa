"""scripts/encrypt_existing_pii.py — encrypts plaintext rows once, skips ciphertext."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from scripts import encrypt_existing_pii as backfill


class _Conn:
    def __init__(self, tables):
        self.tables, self.updates = tables, []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetch(self, sql, *args):
        table = sql.split(" FROM ")[1].split()[0]
        return self.tables.get(table, [])

    async def execute(self, sql, *args):
        self.updates.append((sql, args))


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.mark.asyncio
async def test_encrypts_plaintext_and_skips_existing_ciphertext():
    f = Fernet(Fernet.generate_key())
    already = f.encrypt(b"01700000123").decode()
    conn = _Conn({"appointments": [
        {"id": 1, "patient_name": "রহিম", "patient_mobile": "01700000123"},
        {"id": 2, "patient_name": f.encrypt("করিম".encode()).decode(), "patient_mobile": already},
        {"id": 3, "patient_name": None, "patient_mobile": already},
    ]})
    with (
        patch.object(backfill, "_get_fernet", return_value=f),
        patch.object(backfill, "get_pool", AsyncMock(return_value=_Pool(conn))),
        patch.object(backfill, "close_pool", AsyncMock()),
    ):
        assert await backfill.backfill(dry_run=False) == 0
    assert len(conn.updates) == 1                       # only row 1 needed it
    sql, (row_id, *values) = conn.updates[0]
    assert row_id == 1 and "patient_name" in sql and "patient_mobile" in sql
    assert [f.decrypt(v.encode()).decode() for v in values] == ["রহিম", "01700000123"]


@pytest.mark.asyncio
async def test_dry_run_and_missing_key_change_nothing():
    f = Fernet(Fernet.generate_key())
    conn = _Conn({"sms_log": [{"id": 1, "to_number": "01700000123", "body": "hi"}]})
    with (
        patch.object(backfill, "_get_fernet", return_value=f),
        patch.object(backfill, "get_pool", AsyncMock(return_value=_Pool(conn))),
        patch.object(backfill, "close_pool", AsyncMock()),
    ):
        await backfill.backfill(dry_run=True)
    assert conn.updates == []
    with patch.object(backfill, "_get_fernet", return_value=None):
        assert await backfill.backfill(dry_run=False) == 1

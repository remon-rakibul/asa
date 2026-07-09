"""Field-level AES encryption for patient PII stored in Postgres.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` package.

Set PATIENT_ENCRYPTION_KEY to a Fernet key (generate once with):
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

When the key is empty every call is a transparent no-op, so existing
single-tenant installs keep working.  Hospital/bank deployments MUST set the
key and run the data migration (alembic upgrade head) before going live.

Graceful mixed-state handling: if a row was written before the key was
configured, decrypt_field() returns the raw value rather than raising — this
lets you roll encryption in without a full table lock-step migration.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

_fernet = None
_initialized = False


def _get_fernet():
    global _fernet, _initialized
    if _initialized:
        return _fernet
    from config import settings
    key = (settings.patient_encryption_key or "").strip()
    if key:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode("ascii"))
    _initialized = True
    return _fernet


def encrypt_field(value: Optional[str]) -> Optional[str]:
    """Encrypt a string for storage. Returns None unchanged."""
    if value is None:
        return None
    f = _get_fernet()
    if f is None:
        return value
    return f.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_field(value: Optional[str]) -> Optional[str]:
    """Decrypt a previously encrypted field. Returns plaintext as-is when
    encryption is not configured or the value was stored before the key was set."""
    if value is None:
        return None
    f = _get_fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        # Row written before encryption was enabled — return raw value.
        log.debug("decrypt_field: value is not ciphertext, returning as-is")
        return value

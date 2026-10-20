"""SMS segment counting — what a gateway actually bills.

A message that fits the GSM-7 alphabet costs 160 chars per single SMS (153
per part once concatenated). Anything else — every Bangla message — is sent
as UCS-2: 70 chars single, 67 per part. Characters outside the BMP (emoji)
take two UCS-2 code units. GSM-7 extension characters (€ [ ] { } …) take two
septets. Mirrored in appointment-ui/lib/smsSegments.ts — keep them in sync.
"""

from __future__ import annotations

_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXT = set("^{}\\[~]|€\f")


def sms_encoding(text: str) -> str:
    """'gsm7' or 'ucs2'."""
    return "gsm7" if all(c in _GSM7_BASIC or c in _GSM7_EXT for c in text) else "ucs2"


def sms_segments(text: str) -> int:
    """Number of SMS parts `text` is billed as (0 for an empty message)."""
    if not text:
        return 0
    if sms_encoding(text) == "gsm7":
        units = sum(2 if c in _GSM7_EXT else 1 for c in text)
        single, part = 160, 153
    else:
        units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
        single, part = 70, 67
    if units <= single:
        return 1
    return -(-units // part)

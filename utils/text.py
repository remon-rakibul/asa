"""Text sanitisation for voice output.

Everything streamed to the TTS engine must be plain spoken text — no markdown,
no emojis, no list formatting. Crucially this must be Bangla-safe: we never strip
non-ASCII characters, since Bangla script lives entirely outside ASCII.
"""

import re

import emoji

# Inline/structural markdown markers to remove.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")        # [text](url) -> text
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`+|~~)")      # bold/italic/code/strike
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_LIST_PREFIX = re.compile(r"^\s*([-*+]|[0-9]+[.)])\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")
# Internal slot-format marker from AVAILABLE_SLOTS tool results. The model is
# told to read out only the Bangla label, but small models sometimes echo the
# whole entry — the raw ISO datetime must never reach the patient.
_DATETIME_MARKER = re.compile(r"\[?\s*datetime=[^\]\s]*\s*\]?")

# Bangla digit -> ASCII digit (kept optional; the agent stores ASCII mobiles).
_BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_ASCII_TO_BANGLA = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def normalize_bangla_digits(text: str) -> str:
    """Convert Bangla numerals to ASCII digits."""
    return text.translate(_BANGLA_DIGITS)


def to_bangla_digits(value: int | str) -> str:
    """Convert an integer or ASCII digit string to Bangla numerals."""
    return str(value).translate(_ASCII_TO_BANGLA)


def normalize_bd_mobile(raw: str) -> str:
    """Strip non-digits; return the BD local format (0XXXXXXXXXX, 11 digits).

    A "+880…"/international caller-ID already has 11+ digits after stripping,
    so keeping the last 11 yields "01XXXXXXXXX" correctly. But some SIP
    trunks deliver caller-ID with neither a country code nor the leading
    zero (bare 10 digits) — without prepending "0" the number would never
    match the "0XXXXXXXXXX" format stored everywhere else in this app
    (agent prompts, portal signup), silently disabling caller-ID matching.
    Bangla numerals are normalised first (portal input may use them).
    """
    digits = re.sub(r"\D", "", normalize_bangla_digits(raw or ""))
    digits = digits[-11:] if len(digits) >= 11 else digits
    if len(digits) == 10:
        digits = "0" + digits
    return digits


# Fabricated-listing detection: real slot/doctor listings only ever come from
# tool results, so model text that carries its own numbered list (two or more
# "১." / "2)" items), an internal datetime marker, or an ISO date while a tool
# call is in flight is invented data, never information.
_NUMBERED_ITEM = re.compile(r"(?:^|\s)[\d০-৯]{1,2}[.)]\s")
_ISO_DATE = re.compile(r"(\d{4}|[০-৯]{4})-(\d{2}|[০-৯]{2})-(\d{2}|[০-৯]{2})")


def looks_fabricated_listing(text: str) -> bool:
    """True when model prose contains what can only be invented listing data."""
    if not text:
        return False
    if "datetime=" in text or _ISO_DATE.search(text):
        return True
    return len(_NUMBERED_ITEM.findall(text)) >= 2


def sanitize_text(text: str) -> str:
    """Strip markdown/emoji/formatting while preserving Bangla Unicode."""
    if not text:
        return ""

    text = _DATETIME_MARKER.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _LIST_PREFIX.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _MD_EMPHASIS.sub("", text)

    # Remove only emoji codepoints — leaves Bangla and other scripts intact.
    text = emoji.replace_emoji(text, replace="")

    text = _WHITESPACE.sub(" ", text)
    return text.strip()

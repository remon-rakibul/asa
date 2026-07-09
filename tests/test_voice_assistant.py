"""Tests for voice/assistant.py::_normalize_mobile — caller-ID phone matching
must land on the "0XXXXXXXXXX" 11-digit local format stored everywhere else
in this app (agent prompts, portal signup), or get_patient_by_phone's exact
SQL match silently never recognizes a returning caller."""

from voice.assistant import _normalize_mobile


def test_normalize_international_format_with_country_code():
    assert _normalize_mobile("+8801799887766") == "01799887766"


def test_normalize_bare_11_digit_local_format():
    assert _normalize_mobile("01799887766") == "01799887766"


def test_normalize_bare_10_digit_no_leading_zero():
    # SIP trunk delivers caller-ID with neither country code nor leading
    # zero — must still land on the stored 11-digit format.
    assert _normalize_mobile("1799887766") == "01799887766"


def test_normalize_short_garbage_left_as_is():
    assert _normalize_mobile("12345") == "12345"

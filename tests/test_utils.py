"""Tests for utils/text.py — no mocks or async needed."""

from utils.text import normalize_bangla_digits, sanitize_text


def test_normalize_bangla_digits():
    assert normalize_bangla_digits("০১৭১১") == "01711"


def test_normalize_mixed_digits():
    assert normalize_bangla_digits("017১১000000") == "01711000000"


def test_normalize_latin_unchanged():
    assert normalize_bangla_digits("01711000000") == "01711000000"


def test_sanitize_removes_bold():
    assert sanitize_text("**bold**") == "bold"


def test_sanitize_removes_italic():
    assert sanitize_text("_italic_") == "italic"


def test_sanitize_removes_heading():
    assert "শিরোনাম" in sanitize_text("# শিরোনাম")


def test_sanitize_removes_bullet():
    result = sanitize_text("- আইটেম এক\n- আইটেম দুই")
    assert "আইটেম এক" in result
    assert "-" not in result


def test_sanitize_removes_emoji_preserves_bangla():
    result = sanitize_text("আসসালামু আলাইকুম 😊")
    assert "আসসালামু আলাইকুম" in result
    assert "😊" not in result


def test_sanitize_collapses_whitespace():
    assert sanitize_text("hello   world") == "hello world"


def test_sanitize_preserves_bangla_script():
    text = "আমার নাম রাহেলা বেগম।"
    assert sanitize_text(text) == text


def test_looks_fabricated_listing():
    from utils.text import looks_fabricated_listing

    # Live-observed 2026-07-08: invented slot list streamed beside a tool call.
    assert looks_fabricated_listing(
        "উপলব্ধ সময়: ১.  সোমবার সকাল ১০টা ২.  সোমবার সকাল ১১টা ৩০ মিনিট ৩.  মঙ্গলবার সকাল ১০টা"
    )
    assert looks_fabricated_listing("তারিখ: ২০২৬-০৭-১০ সকাল ১০টায় আসবেন")
    assert looks_fabricated_listing("[datetime=2026-07-10T10:00:00+06:00] সকাল ১০টা")

    # Normal prose — a lone number or age is not a listing.
    assert not looks_fabricated_listing("আপনার বয়স ৩২. মোবাইল নম্বরটা বলবেন?")
    assert not looks_fabricated_listing("আমি এখন সময়সূচি দেখছি, এক মুহূর্ত অপেক্ষা করুন।")
    assert not looks_fabricated_listing("")

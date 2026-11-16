import unicodedata

import pytest

import textkit


@pytest.mark.parametrize(
    "text, want",
    [
        ("Hello, World!", "hello-world"),
        ("Café Déjà", "café-déjà"),
        ("Café", "café"),
        ("Привет, мир!", "привет-мир"),
        ("日本語 テキスト", "日本語-テキスト"),
        ("हिन्दी भाषा", "हिन्दी-भाषा"),
        ("Ünïcode  ÇASE", "ünïcode-çase"),
        ("snake_case and 3.12", "snake-case-and-3-12"),
        ("  -- x --  ", "x"),
        ("---", ""),
        ("", ""),
    ],
)
def test_slugify_unicode(text, want):
    got = textkit.slugify_unicode(text)
    assert type(got) is str
    assert got == want


@pytest.mark.parametrize(
    "text, want",
    [
        ("J" + chr(0x30C), chr(0x1F0)),
        ("T" + chr(0x308), chr(0x1E97)),
        ("W" + chr(0x30A), chr(0x1E98)),
        ("H" + chr(0x331), chr(0x1E96)),
        ("Caf" + chr(0xC9) + " J" + chr(0x30C), "caf" + chr(0xE9) + "-" + chr(0x1F0)),
    ],
)
def test_slugify_unicode_result_is_nfc_after_lower(text, want):
    # lower() leaves base + combining mark, which has a precomposed NFC form
    got = textkit.slugify_unicode(text)
    assert unicodedata.is_normalized("NFC", got)
    assert got == want
    assert textkit.slugify_unicode(got) == got

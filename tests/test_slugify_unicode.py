import pytest

import textkit


@pytest.mark.parametrize(
    "text, want",
    [
        ("Hello, World!", "hello-world"),
        ("Café Déjà", "café-déjà"),
        ("Café", "café"),
        ("Привет, мир!", "привет-мир"),
        ("日本語 テキスト", "日本語-テキスト"),
        ("हिन्दी भाषा", "हिन्दी-भाषा"),
        ("Ünïcode  ÇASE", "ünïcode-çase"),
        ("snake_case and 3.12", "snake-case-and-3-12"),
        ("  -- x --  ", "x"),
        ("---", ""),
        ("", ""),
        ("İ", "i̇"),
        ("İstanbul", "i̇stanbul"),
        ("Aİ B", "ai̇-b"),
        ("Café", "café"),
    ],
)
def test_slugify_unicode(text, want):
    got = textkit.slugify_unicode(text)
    assert type(got) is str
    assert got == want
    assert textkit.slugify_unicode(got) == got

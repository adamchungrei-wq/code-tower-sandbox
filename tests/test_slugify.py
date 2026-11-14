import pytest

import textkit


@pytest.mark.parametrize(
    "text, want",
    [
        ("Hello, World!", "hello-world"),
        ("  Many   spaces  ", "many-spaces"),
        ("Already-a-slug", "already-a-slug"),
        ("C++ & Python 3.12", "c-python-3-12"),
        ("---", ""),
        ("", ""),
        ("a__b", "a-b"),
        ("Tab\tand\nnewline", "tab-and-newline"),
        ("Café Déjà", "cafe-deja"),
        ("Café Déjà", "cafe-deja"),
        ("Über Ångström ñ", "uber-angstrom-n"),
        ("éé", "ee"),
    ],
)
def test_slugify(text, want):
    got = textkit.slugify(text)
    assert type(got) is str
    assert got == want

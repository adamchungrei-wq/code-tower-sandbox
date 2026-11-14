import pytest

import textkit


@pytest.mark.parametrize(
    "text, want",
    [
        ("one two  three", 3),
        ("", 0),
        ("   ", 0),
        ("hello", 1),
        ("tabs\tand\nnew lines", 4),
        ("don't stop", 2),
        ("  padded  ", 1),
        ("well-known", 1),
        ("state-of-the-art", 1),
        ("a well-known fact", 3),
        ("A state-of-the-art, well-known tool.", 4),
        ("long-term and short-term", 3),
    ],
)
def test_word_count(text, want):
    got = textkit.word_count(text)
    assert type(got) is int
    assert got == want

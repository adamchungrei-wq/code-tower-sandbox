import pytest

import textkit


@pytest.mark.parametrize(
    "text, limit, want",
    [
        ("abcdef", 4, "abc…"),
        ("abcd", 4, "abcd"),
        ("abc", 10, "abc"),
        ("abcdef", 1, "…"),
        ("", 3, ""),
    ],
)
def test_truncate(text, limit, want):
    assert textkit.truncate(text, limit) == want


@pytest.mark.parametrize("limit", [3.0, "3", None, True, False, [3]])
def test_truncate_non_int_limit(limit):
    with pytest.raises(TypeError):
        textkit.truncate("abc", limit)


@pytest.mark.parametrize("limit", [0, -1])
def test_truncate_bad_limit(limit):
    with pytest.raises(ValueError):
        textkit.truncate("abc", limit)

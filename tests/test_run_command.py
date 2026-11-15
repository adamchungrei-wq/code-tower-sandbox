import pytest

import textkit


@pytest.mark.parametrize(
    "argv, want",
    [
        (["slugify", "Hello, World!"], "hello-world"),
        (["word-count", "one two  three"], "3"),
        (["word-count", ""], "0"),
        (["truncate", "hello world", "6"], "hello…"),
        (["truncate", "hi", "6"], "hi"),
        (["normalize", "  hello   big\n world "], "hello big world"),
    ],
)
def test_commands(argv, want):
    assert textkit.run_command(argv) == want


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nope", "x"],
        ["slugify"],
        ["slugify", "a", "b"],
        ["truncate", "hello", "six"],
        ["truncate", "hello"],
        ["truncate", "hello", "0"],
    ],
)
def test_bad_input_raises_value_error(argv):
    with pytest.raises(ValueError, match=r"\S"):
        textkit.run_command(argv)

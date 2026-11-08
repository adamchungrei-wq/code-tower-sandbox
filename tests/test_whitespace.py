import textkit
from textkit import normalize_whitespace


def test_collapses_runs_and_strips_ends():
    assert normalize_whitespace("  a \t b\n\nc  ") == "a b c"


def test_empty_and_blank():
    assert normalize_whitespace("") == ""
    assert normalize_whitespace(" \n\t ") == ""


def test_exported_from_package():
    assert "normalize_whitespace" in textkit.__all__

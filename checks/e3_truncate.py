"""Exit check E3: truncate(text, limit) shortens text to at most `limit` characters, ending with "…". Exit 0 = pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from textkit import truncate  # noqa: E402  (ImportError = the row is still open)

CASES = [
    (("abcdef", 4), "abc…"),
    (("abcd", 4), "abcd"),
    (("abc", 10), "abc"),
    (("abcdef", 1), "…"),
    (("", 3), ""),
]
for args, want in CASES:
    got = truncate(*args)
    assert got == want, f"truncate{args!r} returned {got!r}, expected {want!r}"
    assert len(got) <= args[1], f"truncate{args!r} is longer than the limit"

try:
    truncate("abc", 0)
except ValueError:
    pass
else:
    raise AssertionError("truncate(text, 0) should raise ValueError")
print("E3 pass")

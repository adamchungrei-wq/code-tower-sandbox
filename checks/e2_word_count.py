"""Exit check E2: word_count(text) counts words (runs of non-whitespace). Exit 0 = pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from textkit import word_count  # noqa: E402  (ImportError = the row is still open)

CASES = {
    "one two  three": 3,
    "": 0,
    "   ": 0,
    "hello": 1,
    "tabs\tand\nnew lines": 4,
    "don't stop": 2,
}
for text, want in CASES.items():
    got = word_count(text)
    assert type(got) is int and got == want, f"word_count({text!r}) returned {got!r}, expected {want!r}"
print("E2 pass")

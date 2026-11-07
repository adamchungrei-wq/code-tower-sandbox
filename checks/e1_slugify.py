"""Exit check E1: slugify(text) turns text into a URL slug. Exit 0 = pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from textkit import slugify  # noqa: E402  (ImportError = the row is still open)

CASES = {
    "Hello, World!": "hello-world",
    "  Many   spaces  ": "many-spaces",
    "Already-a-slug": "already-a-slug",
    "C++ & Python 3.12": "c-python-3-12",
    "---": "",
    "": "",
}
for text, want in CASES.items():
    got = slugify(text)
    assert got == want, f"slugify({text!r}) returned {got!r}, expected {want!r}"
print("E1 pass")

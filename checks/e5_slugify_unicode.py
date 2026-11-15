"""Exit check E5: slugify_unicode(text) makes a URL slug that keeps letters and digits of any script. Exit 0 = pass.
Results must be exactly str: an object that claims to equal anything can't pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from textkit import slugify_unicode  # noqa: E402  (ImportError = the row is still open)

CASES = {
    "Hello, World!": "hello-world",
    "Café Déjà": "café-déjà",
    "Привет, мир!": "привет-мир",
    "日本語 テキスト": "日本語-テキスト",
    "Ünïcode  ÇASE": "ünïcode-çase",
    "snake_case and 3.12": "snake-case-and-3-12",
    "école": "école",
    "---": "",
    "": "",
}
for text, want in CASES.items():
    got = slugify_unicode(text)
    assert type(got) is str and got == want, f"slugify_unicode({text!r}) returned {got!r}, expected {want!r}"
print("E5 pass")

"""Exit check E4: README.md has a "## Usage" section showing all four functions, each importable. Exit 0 = pass."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import textkit  # noqa: E402

FUNCTIONS = ("normalize_whitespace", "slugify", "word_count", "truncate")
missing_code = [f for f in FUNCTIONS if not callable(getattr(textkit, f, None))]
assert not missing_code, f"textkit doesn't export: {missing_code}"

readme = (ROOT / "README.md").read_text(encoding="utf-8")
match = re.search(r"^## Usage[ \t]*$(.*?)(?=^## |\Z)", readme, re.M | re.S)
assert match, "README.md has no '## Usage' section"
missing_docs = [f for f in FUNCTIONS if f"{f}(" not in match.group(1)]
assert not missing_docs, f"the Usage section doesn't show: {missing_docs}"
print("E4 pass")

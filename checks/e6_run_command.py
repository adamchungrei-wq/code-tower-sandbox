"""Exit check E6: run_command(argv) is the small command-line tool's core. It takes the words of a command line
(the command name, then its arguments) and returns the text to show. It prints nothing and exits nothing, so a thin
terminal wrapper can be added later, outside the tower's rows. Exit 0 = pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from textkit import run_command  # noqa: E402  (ImportError = the row is still open)

CASES = [
    (["slugify", "Hello, World!"], "hello-world"),
    (["word-count", "one two  three"], "3"),
    (["truncate", "hello world", "6"], "hello…"),
    (["normalize", "  hello   big\n world "], "hello big world"),
]
for argv, want in CASES:
    got = run_command(list(argv))
    assert type(got) is str and got == want, f"run_command({argv!r}) returned {got!r}, expected {want!r}"

BAD = [[], ["nope", "x"], ["slugify"], ["slugify", "a", "b"], ["truncate", "hello", "six"], ["truncate", "hello"]]
for argv in BAD:
    try:
        result = run_command(list(argv))
    except ValueError as err:
        assert str(err).strip(), f"run_command({argv!r}) raised ValueError with no message"
    else:
        raise AssertionError(f"run_command({argv!r}) returned {result!r}, expected ValueError")
print("E6 pass")

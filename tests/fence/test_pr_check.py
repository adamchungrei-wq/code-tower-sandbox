import json
from pathlib import Path

import pytest

import pr_check

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / "rules" / "project.json").read_text(encoding="utf-8"))
BODY = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
CODE_DIFF = "--- a/src/textkit/slug.py\n+++ b/src/textkit/slug.py\n+def slugify(text):\n+    return text\n"


def problems(branch, files, diff=CODE_DIFF, body=BODY, labels=(), code=None):
    if code is None:      # the PR guard copies each changed src/ file from the PR head; mimic that from the diff
        code = {f: "".join(l[1:] + "\n" for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
                for f in files if f.startswith("src/") and f.endswith(".py")}
    return pr_check.problems(branch, files, diff, body, RULES, labels, code=code)


def test_clean_row_pr_passes():
    assert problems("tower/e1", ["src/textkit/slug.py", "tests/test_slug.py"]) == []
    assert problems("claude/tower-e1", ["src/textkit/slug.py"]) == []


def test_any_pr_that_changes_the_loops_rules_needs_adams_label():
    for branch in ("tower/e1", "rules/caps", "feature/x"):
        found = problems(branch, ["rules/project.json"])
        assert any("rules-approved" in p for p in found), branch
    assert problems("rules/caps", ["rules/project.json"], labels=["rules-approved"]) == []


def test_row_pr_never_changes_rules_even_with_the_label():
    found = problems("tower/e1", ["checks/e1_slugify.py"], labels=["rules-approved"])
    assert any("row PR may never change" in p for p in found)


def test_row_pr_stays_inside_worker_paths():
    assert any("outside the worker paths" in p for p in problems("tower/e1", ["setup.cfg"]))


def test_renamed_workflow_is_seen_as_delete_plus_add():
    # pr-guard.yml lists files with --no-renames, so a moved workflow shows both paths
    found = problems("tower/e9", [".github/workflows/pr-guard.yml", "src/textkit/notes.yml"])
    assert any("loop's own rules" in p for p in found)


def test_size_cap_counts_changed_lines_not_headers():
    big = "--- a/src/x.py\n+++ b/src/x.py\n" + "".join(f"+line {i}\n" for i in range(201))
    assert any("changed lines" in p for p in problems("tower/e1", ["src/x.py"], diff=big))
    assert problems("tower/e1", ["src/x.py"], diff="--- a/src/x.py\n+++ b/src/x.py\n+ok\n") == []


def test_secrets_in_lines_files_or_summary_fail():
    diff = CODE_DIFF + "+KEY = 'sk-ant-" + "a" * 30 + "'\n"
    assert any("secret-like text in added lines" in p for p in problems("tower/e1", ["src/textkit/slug.py"], diff=diff))
    assert any("credential file" in p for p in problems("rules/x", [".env"], labels=["rules-approved"]))
    body = BODY.replace("## Risks", "ghp_" + "a" * 36 + "\n\n## Risks")
    assert any("in the PR summary" in p for p in problems("tower/e1", ["src/textkit/slug.py"], body=body))


def test_summary_format():
    found = problems("tower/e1", ["src/textkit/slug.py"], body="fixed it")
    assert any("What changed" in p for p in found)
    assert any("Adam approves merge" in p for p in found)


def test_tool_footer_after_the_approval_line_is_fine():
    body = BODY + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\nhttps://claude.ai/code/session_x\n"
    assert problems("tower/e1", ["src/textkit/slug.py"], body=body) == []


def test_approval_must_be_its_own_line_after_undo():
    body = BODY.replace("\nAdam approves merge", "\nWe hope Adam approves merge soon")
    assert any("Adam approves merge" in p for p in problems("tower/e1", ["src/textkit/slug.py"], body=body))


def test_row_code_that_could_fake_a_check_is_flagged():
    diff = ("--- /dev/null\n+++ b/src/textkit/zz_helpers.py\n+import os, sys\n+print(sys.argv[0])\n+os._exit(0)\n")
    found = problems("tower/e1", ["src/textkit/zz_helpers.py"], diff=diff)
    assert any("exits early" in p for p in found)
    assert problems("tower/e1", ["src/textkit/slug.py"]) == []


# --- re-check #3 should-fix 7: row code under src/ is read, never run, and can't fake a check ------------
SLUG = ("import re\nimport unicodedata\n\n__all__ = ['slugify']\n\n\ndef slugify(text: str) -> str:\n"
        "    \"\"\"Lower-case, ASCII, words joined by hyphens.\"\"\"\n"
        "    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()\n"
        "    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n")
FAKES = {
    "aliased exit": "import os as o\nprint('E1 pass')\no._exit(0)\n",
    "from-import exit": "from os import _exit as q\nq(0)\n",
    "sys exit": "import sys\nsys.exit(0)\n",
    "raise SystemExit": "raise SystemExit(0)\n",
    "builtins exit": "exit(0)\n",
    "print the pass line": "def slugify(t):\n    print('E1 pass')\n",
    "dunder walk": "x = (lambda: 0).__globals__['__builtins__']\n",
    "frame walk": "g = (i for i in [])\nb = g.gi_frame.f_globals\n",
    "module chain": "import re\nre.enum.sys.modules['os'].system('x')\n",
    "string formatter": "import string\nstring.Formatter().get_field('0.__globals__', [len], {})\n",
    "getattr": "import re\ngetattr(re, 'compile')\n",
    "dynamic import": "m = __import__('os')\n",
    "importlib": "import importlib\nimportlib.import_module('os')\n",
    "open a file": "open('/dev/stdout', 'w').write('E1 pass')\n",
    "match private": "match 1:\n    case object(__class__=c):\n        pass\n",
    "star import": "from re import *\n",
}


def code_problems_for(text, path="src/textkit/slug.py"):
    diff = f"--- /dev/null\n+++ b/{path}\n" + "".join("+" + l + "\n" for l in text.splitlines())
    return problems("tower/e1", [path], diff=diff, labels=(), code={path: text})


def test_plain_row_code_passes_the_code_rule():
    assert code_problems_for(SLUG) == []


@pytest.mark.parametrize("name", sorted(FAKES))
def test_code_that_could_fake_a_check_fails(name):
    found = code_problems_for(FAKES[name])
    assert any("fake a check" in p or "exits early" in p for p in found), (name, found)


def test_changed_src_code_the_guard_could_not_read_fails():
    diff = "--- /dev/null\n+++ b/src/textkit/slug.py\n+x = 1\n"
    found = problems("tower/e1", ["src/textkit/slug.py"], diff=diff, code={})
    assert any("couldn't read" in p for p in found)


def test_row_prs_never_add_pytest_config_or_compiled_files():
    for f in ("tests/conftest.py", "src/conftest.py", "tests/pytest.ini", "src/textkit/x.cpython-312.pyc", "src/x.pth"):
        found = problems("tower/e1", [f], diff="--- /dev/null\n+++ b/x\n+x\n", code={})
        assert any("pytest config" in p for p in found), f


def test_the_package_auto_export_file_is_part_of_the_rules():
    found = problems("tower/e1", ["src/textkit/__init__.py"], code={"src/textkit/__init__.py": "x = 1\n"})
    assert any("never change the loop's own rules" in p for p in found)


def test_new_commits_after_the_label_need_a_fresh_label():
    assert problems("rules/caps", ["rules/project.json"], labels=["rules-approved"]) == []
    found = pr_check.problems("rules/caps", ["rules/project.json"], CODE_DIFF, BODY, RULES, ["rules-approved"],
                              event="synchronize")
    assert any("re-adds the label" in p for p in found)
    assert pr_check.problems("rules/caps", ["rules/project.json"], CODE_DIFF, BODY, RULES, ["rules-approved"],
                             event="labeled") == []

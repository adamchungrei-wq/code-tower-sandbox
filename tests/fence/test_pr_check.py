import json
from pathlib import Path

import pr_check

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / "rules" / "project.json").read_text(encoding="utf-8"))
BODY = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
CODE_DIFF = "--- a/src/textkit/slug.py\n+++ b/src/textkit/slug.py\n+def slugify(text):\n+    return text\n"


def problems(branch, files, diff=CODE_DIFF, body=BODY):
    return pr_check.problems(branch, files, diff, body, RULES)


def test_clean_tower_pr_passes():
    assert problems("tower/e1", ["src/textkit/slug.py", "tests/test_slug.py"]) == []


def test_tower_pr_cannot_change_the_loops_rules():
    found = problems("tower/e1", ["src/textkit/slug.py", "checks/e1_slugify.py"])
    assert any("loop's own rules" in p for p in found)


def test_tower_pr_stays_inside_worker_paths():
    found = problems("tower/e1", ["setup.cfg"])
    assert any("outside the worker paths" in p for p in found)


def test_rules_pr_may_change_rules():
    assert problems("rules/caps", ["rules/project.json"]) == []


def test_size_cap_counts_changed_lines_not_headers():
    big = "--- a/src/x.py\n+++ b/src/x.py\n" + "".join(f"+line {i}\n" for i in range(201))
    assert any("changed lines" in p for p in problems("tower/e1", ["src/x.py"], diff=big))
    assert problems("tower/e1", ["src/x.py"], diff="--- a/src/x.py\n+++ b/src/x.py\n+ok\n") == []


def test_secrets_in_added_lines_fail():
    diff = CODE_DIFF + "+KEY = 'sk-ant-" + "a" * 30 + "'\n"
    assert any("secret-like" in p for p in problems("tower/e1", ["src/textkit/slug.py"], diff=diff))
    assert any("credential file" in p for p in problems("rules/x", [".env"]))


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

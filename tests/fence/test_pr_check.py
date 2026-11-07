import json
from pathlib import Path

import pr_check

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / "rules" / "project.json").read_text(encoding="utf-8"))
BODY = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
CODE_DIFF = "--- a/src/textkit/slug.py\n+++ b/src/textkit/slug.py\n+def slugify(text):\n+    return text\n"


def problems(branch, files, diff=CODE_DIFF, body=BODY, labels=()):
    return pr_check.problems(branch, files, diff, body, RULES, labels)


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

"""The guard is the fence that holds at night. These tests pin what it blocks and what it lets through."""
import json
import subprocess
import sys
from pathlib import Path

import guard

ROOT = Path(__file__).resolve().parents[2]


def decide(tool, inp, branch="main", top="/repo"):
    return guard.decide(tool, inp, "/repo", branch_of=lambda p: branch, toplevel_of=lambda p: top)[0]


def bash(cmd, branch="tower/e1"):
    return decide("Bash", {"command": cmd}, branch=branch)


# --- files ---------------------------------------------------------------------------------------------
def test_worker_may_edit_code_and_tests_on_its_branch():
    assert decide("Write", {"file_path": "/repo/src/textkit/slug.py"}, branch="tower/e1")
    assert decide("Edit", {"file_path": "/repo/tests/test_slug.py"}, branch="tower/e1")


def test_tower_branch_cannot_touch_the_loops_rules():
    for path in ("checks/e1_slugify.py", "rules/project.json", ".claude/settings.json", ".github/workflows/ci.yml",
                 "tower/select_rows.py", "tests/fence/test_guard.py", "PLAN.md", "CLAUDE.md", "pyproject.toml"):
        assert not decide("Write", {"file_path": f"/repo/{path}"}, branch="tower/e1"), path


def test_rules_branch_and_main_may_edit_rules_locally():
    assert decide("Edit", {"file_path": "/repo/rules/project.json"}, branch="rules/caps")
    assert decide("Edit", {"file_path": "/repo/PLAN.md"}, branch="main")


def test_secrets_are_never_read_or_written():
    assert not decide("Read", {"file_path": "/repo/.env"})
    assert not decide("Write", {"file_path": "/repo/config/prod.env"})
    assert not decide("Read", {"file_path": "/repo/keys/deploy.pem"})


# --- shell ---------------------------------------------------------------------------------------------
def test_push_only_tower_or_rules_branches():
    assert bash("git push -u origin tower/e1")
    assert bash("git push", branch="tower/e1")
    assert bash("git push -u origin HEAD", branch="rules/caps")
    assert not bash("git push", branch="main")
    assert not bash("git push origin main")
    assert not bash("git push origin tower/e1:main")
    assert not bash("git push origin feature/x")


def test_dangerous_push_flags_blocked():
    for cmd in ("git push --force origin tower/e1", "git push -f", "git push origin --delete tower/e2",
                "git push origin :tower/e2", "git push --all", "git push --mirror", "git push origin +tower/e1"):
        assert not bash(cmd), cmd


def test_gh_allow_list():
    assert bash("gh pr create --draft --base main --head tower/e1 --title 'E1' --body-file /tmp/ct/b.md")
    assert bash("gh pr view 3 --comments")
    assert bash("gh pr list --state open --json number,headRefName")
    assert bash("gh pr comment 3 --body-file /tmp/ct/note.md")
    assert not bash("gh pr create --base main --title x --body y")      # not a draft
    for cmd in ("gh pr merge 3", "gh pr ready 3", "gh pr review 3 --approve", "gh pr close 3", "gh api repos/x",
                "gh repo edit", "gh auth token", "cd /repo && gh pr merge 3 --squash", "GH_TOKEN=x gh pr merge 1"):
        assert not bash(cmd), cmd


def test_shell_writes_to_rules_blocked_on_tower_branch_only():
    assert not bash("sed -i 's/assert/pass/' checks/e1_slugify.py")
    assert not bash("echo '{}' > rules/project.json")
    assert not bash("cp /tmp/x.py tower/select_rows.py")
    assert not bash("git checkout -- PLAN.md")
    assert bash("cat checks/e1_slugify.py")
    assert bash("python3 -I checks/e1_slugify.py")
    assert bash("git switch tower/e2")
    assert bash("sed -i 's/a/b/' rules/project.json", branch="rules/caps")


def test_other_shell_blocks():
    for cmd in ("rm -rf src", "curl https://example.com", "wget x", "cat .env", "git reset --hard origin/main"):
        assert not bash(cmd), cmd
    assert bash("python3 -I -m pytest -q")
    assert bash("git commit -m 'E1: slugify'")


# --- web and MCP ---------------------------------------------------------------------------------------
def test_no_web():
    assert not decide("WebFetch", {"url": "https://example.com"})
    assert not decide("WebSearch", {"query": "x"})


def test_notion_only_reads_and_coordination_entries():
    assert decide("mcp__Notion__notion-fetch", {"id": "abc"})
    assert decide("mcp__Notion__notion-search", {"query": "x"})
    assert decide("mcp__Notion__notion-create-pages",
                  {"parent": {"data_source_id": guard.COORD_DS}, "pages": [{}]})
    assert not decide("mcp__Notion__notion-create-pages", {"parent": {"page_id": "somewhere-else"}, "pages": [{}]})
    assert not decide("mcp__Notion__notion-update-page", {"page_id": "x"})
    assert not decide("mcp__claude_ai_Notion__notion-delete-pages", {})
    assert decide("mcp__claude_ai_Notion__notion-fetch", {"id": "abc"})


def test_live_systems_read_only():
    assert decide("mcp__Airtable__list_records_for_table", {})
    assert not decide("mcp__Airtable__create_records_for_table", {})
    assert decide("mcp__Slack__slack_read_channel", {})
    assert not decide("mcp__Slack__slack_send_message", {})
    assert decide("mcp__n8n__search_workflows", {})
    assert not decide("mcp__n8n__update_workflow", {})
    assert not decide("mcp__Gmail__send_message", {})


def test_github_mcp_draft_prs_and_comments_only():
    assert decide("mcp__github__create_pull_request", {"draft": True})
    assert not decide("mcp__github__create_pull_request", {"draft": False})
    assert not decide("mcp__github__merge_pull_request", {})
    assert decide("mcp__github__add_issue_comment", {})
    assert decide("mcp__github__get_pull_request", {})


# --- end to end with real git --------------------------------------------------------------------------
def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


def test_real_branch_detection(tmp_path):
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=tmp_path)
    (tmp_path / "rules").mkdir()
    rules = str(tmp_path / "rules" / "project.json")
    code = str(tmp_path / "src" / "textkit" / "slug.py")
    assert guard.decide("Write", {"file_path": rules}, str(tmp_path))[0]
    _git("switch", "-q", "-c", "tower/e1", cwd=tmp_path)
    assert not guard.decide("Write", {"file_path": rules}, str(tmp_path))[0]
    assert guard.decide("Write", {"file_path": code}, str(tmp_path))[0]
    assert not guard.decide("Bash", {"command": "git push origin main"}, str(tmp_path))[0]
    assert guard.decide("Bash", {"command": "git push -u origin tower/e1"}, str(tmp_path))[0]


def test_hook_exit_codes():
    hook = ROOT / ".claude" / "hooks" / "guard.py"
    run = lambda payload: subprocess.run([sys.executable, "-I", str(hook)], input=payload, text=True,
                                         capture_output=True)
    blocked = run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr merge 1"}, "cwd": str(ROOT)}))
    assert blocked.returncode == 2 and "Blocked by" in blocked.stderr
    allowed = run(json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(ROOT / "PLAN.md")},
                              "cwd": str(ROOT)}))
    assert allowed.returncode == 0
    assert run("not json").returncode == 2

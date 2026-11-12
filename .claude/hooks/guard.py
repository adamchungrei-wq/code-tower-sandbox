"""PreToolUse guard for a Code Tower repo. V0.6: exact command forms and confined files, after five independent
checks (2026-11-07).

.claude/settings.json allows or denies tools by name; this hook looks inside each call. The hook command in
settings.json runs the copy committed on refs/remotes/origin/main (the working copy only while main has no guard;
a checkout with no origin/main blocks everything). Exit 0 = allow (the normal permission rules still apply) ·
exit 2 = block, reason on stderr. Any error blocks. Hooks also run when a scheduled run auto-approves tools.

Principles: allow what the tower needs, in listed shapes; refuse everything else.
1. Switches: main's rules/project.json decides. With "mode" other than "on", or rung 0, a run may read, write
   runs/ct/ and file its one entry, but starts no workers and makes no commits, pushes, PRs or comments. Pushes,
   PRs, comments and new workers also need main fetched in the last 10 minutes, so a switch Adam flips on GitHub
   reaches a run that is already going.
2. Tools: the listed built-in tools. Agent only as a worker or checker in its own worktree, with no other options;
   a checker only reads. Skill only as code-tower. Notion fetch/search/query and one page in the Coordination
   Entries data source. No other app, no web.
3. Shell: a command runs only if the whole command matches one of FORMS: one command per call, plain words and
   simple quoted text, no chains, cd, $, ~, globs or extra options. The tower's own checkout stays on origin/main;
   any checkout switches only to a commit whose .claude/settings.json is main's (Claude Code reloads hooks when
   that file changes).
4. Files: read and searched only inside this repo's checkout and its worktrees (plus this session's own saved
   tool results). Written only in the caller's own checkout: the tower writes runs/ct/; a worker, in its
   worktree, writes src/ and tests/ (by file type), README.md and runs/ct/. Never the loop's own rules
   (PROTECTED), agent instructions (CLAUDE.md, .claude/ at any depth), pytest config, compiled files or .git.
5. git push only as `git push [-u] origin HEAD:[refs/heads/]<prefix><row>`, with main's prefix and a row in
   main's PLAN.md; refused if the branch changes PROTECTED files or agent instructions, if git config could
   redirect it, or if origin isn't https://github.com/<repo>. gh: `gh auth status`, REST reads, one draft-PR shape
   and one comment shape on this repo. A PR also needs a row with no open PR and a free slot under tonight's cap:
   the guard counts GitHub's PRs plus the PRs it allowed in the last 24 hours, under a lock, so parallel calls
   can't all pass.
6. No secrets: credential files and .git internals are never read; commit messages, PR titles, bodies and
   entries are refused if they hold token-like text, images or HTML character references, and may link only to
   this repo on GitHub, to Notion and to Claude Code's own pages.
Known gap (disclosed in PR #1): code a worker writes and then runs (a test, say) can do anything the session's own
credentials allow. The PR guard reads row code under src/ before merge; only GitHub branch protection (a public
repo or a paid plan) or an OS sandbox closes the gap fully.
"""
import fcntl
import html
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAIN = "refs/remotes/origin/main"
COORD_DS = "eb8da925-cf1c-4689-af1d-830f77fc5e96"
PROTECTED = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/", ".claude/agents/",
             ".claude/skills/", ".claude/commands/", ".claude/CLAUDE.md", ".github/", ".gitignore", ".mcp.json",
             "rules/", "checks/", "tower/", "tests/fence/", "PLAN.md", "CLAUDE.md", "CLAUDE.local.md",
             "pyproject.toml", "src/textkit/__init__.py")
ROW_BRANCH = re.compile(r"^(tower/|claude/tower-)[a-z0-9]+$")
BODY_FILE = re.compile(r"^runs/ct/[A-Za-z0-9_-]{1,60}\.md$")
WORKER_PATHS = ("src/", "tests/", "runs/ct/")      # plus README.md
TOWER_PATHS = ("runs/ct/",)
WRITABLE_EXT = re.compile(r"\.(py|md|txt|json|csv)$")
# Files that change how Python or pytest starts (config, plugins, compiled code): never written by a run.
STARTUP_FILE = re.compile(r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml|sitecustomize\.py|"
                          r"usercustomize\.py|__pycache__/.*|[^/]+\.(pth|pyc|so|pyd))$", re.I)
# Files Claude Code reads as instructions or settings, at any depth: a worker must never plant them.
AGENT_FILE = re.compile(r"(^|/)(\.claude(/|$)|CLAUDE\.md$|CLAUDE\.local\.md$|AGENTS\.md$|\.mcp\.json$)", re.I)
PLAN_BLOCK = re.compile(r"```json exit-checks[ \t]*\n(.*?)\n```", re.S)
AGENTS = {"worker", "checker"}
AGENT_KEYS = {"subagent_type", "isolation", "description", "prompt", "run_in_background"}
TOOL_RESULTS = "/tmp/claude-tool-results/"        # claude.ai containers keep large tool outputs here
FRESH_SECONDS = 600                                 # main must have been fetched this recently for outward actions
GREP_ROOTS = ("src", "tests", "checks", "tower", "rules", "runs/ct", "README.md", "PLAN.md", "CLAUDE.md")
ENTITY = re.compile(r"&#|&[A-Za-z][A-Za-z0-9]{1,31};")
IMAGE = re.compile(r"!\[|<\s*(img|image|picture|source|video|audio|iframe|embed|object|svg|link|meta)\b|\bsrc\s*=|"
                   r"^\s{0,3}\[[^\]\n]+\]:\s*\S", re.I | re.M)
MARKER = "<!-- code-tower -->"
GH_GET_FIELDS = {"state", "per_page", "page", "sort", "direction"}
BUILTIN_TOOLS = {"Read", "Grep", "Glob", "LS", "Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "BashOutput",
                 "KillShell", "KillBash", "Agent", "Task", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                 "TaskOutput", "TaskStop", "TodoWrite", "SendMessage", "Skill", "ToolSearch", "AskUserQuestion"}
# Not Monitor or SlashCommand (they can run shell commands), not plan mode or EnterWorktree (unused, and they
# take options this guard doesn't inspect).
SECRETS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bpat[A-Za-z0-9]{14}\.[a-f0-9]{64}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b(ntn_[A-Za-z0-9]{40,}|secret_[A-Za-z0-9]{43})\b"),
)
TOKENISH = re.compile(r"[A-Za-z0-9_+/=-]{24,}")     # a long run mixing upper case, lower case and digits
SESSION_LINK = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
SCHEME = re.compile(r"\b(?:https?|ftps?|wss?|data|javascript|vbscript|file|blob)\s*:", re.I)
LINKS_OK = ("https://app.notion.com/", "https://www.notion.so/", "https://notion.so/",
            "https://claude.com/claude-code", "https://claude.ai/code/")
MAX_TEXT = 30000
SECRET_PATH = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|[^/]*\.env|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*|id_ed25519[^/]*|credentials[^/]*|"
    r"\.credentials\.json|\.claude\.json|settings\.local\.json|\.git-credentials|\.netrc|\.npmrc|\.pypirc|"
    r"\.boto|hosts\.yml)$|(^|/)(\.ssh|\.aws|\.docker|\.gnupg|\.kube|\.config/gh|\.config/gcloud)(/|$)|"
    r"^/proc(/|$)|^/sys(/|$)|^/(var/)?run/secrets(/|$)|^/etc/g?shadow$", re.I)
SAFE_GREP_GLOB = re.compile(r"(\*\*/)?([A-Za-z0-9_-]+/)*(\*\*/)?[A-Za-z0-9_*-]*\.(py|md|txt|json|csv|toml|yml|yaml|"
                            r"cfg|ini|rst)")


def block(why):
    return False, why


# ---------------------------------------------------------------------------------------------- environment
class Env:
    """Git, GitHub and file lookups. Tests replace this with a stub."""

    def __init__(self, project_dir="", session_id="", agent_type=""):
        self.project_dir = project_dir
        self.session_id = session_id
        self.agent_type = agent_type
        self._cache = {}

    def _git(self, args, path):
        p = Path(path) if path else None
        while p is not None and not p.exists() and p != p.parent:
            p = p.parent
        if p is None or not p.exists():
            return None
        where = str(p if p.is_dir() else p.parent)
        key = (tuple(args), where)
        if key in self._cache:
            return self._cache[key]
        try:
            proc = subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        out = proc.stdout if proc.returncode == 0 else None
        if args[:1] in (["rev-parse"], ["show"], ["config"], ["remote"]):      # stable within one hook call
            self._cache[key] = out
        return out

    def toplevel(self, path):
        out = self._git(["rev-parse", "--show-toplevel"], path)
        return out.strip() if out else ""

    def main_file(self, top, rel):
        """A file as committed on origin/main (full ref, so a local branch named origin/main can't shadow it);
        the working copy before main has it."""
        out = self._git(["show", f"{MAIN}:{rel}"], top)
        if out is not None:
            return out
        try:
            return (Path(top) / rel).read_text(encoding="utf-8")
        except OSError:
            return ""

    def rules(self, path):
        top = self.toplevel(path) or self.project_dir
        try:
            data = json.loads(self.main_file(top, "rules/project.json") or "{}") if top else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def repo(self, path):
        return str(self.rules(path).get("github_repo", ""))

    def protected(self, path):
        return PROTECTED + tuple(str(p) for p in self.rules(path).get("tamper_paths", ()))

    def plan_ids(self, path):
        """Row IDs in main's PLAN.md exit-check block (empty if it can't be read)."""
        top = self.toplevel(path) or self.project_dir
        match = PLAN_BLOCK.search(self.main_file(top, "PLAN.md") if top else "")
        try:
            return {str(c.get("id", "")) for c in json.loads(match.group(1)).get("checks", [])} if match else set()
        except (ValueError, AttributeError):
            return set()

    def diff_names(self, where, src):
        out = self._git(["diff", "--name-only", "--no-renames", f"{MAIN}...{src}"], where)
        return None if out is None else [l.strip() for l in out.splitlines() if l.strip()]

    def push_redirects(self, where):
        """Git config that could send a push somewhere else ('' if none; None if git can't answer).
        Claude Code's cloud sessions inject `url.https://github.com/.insteadof git@github.com:` (and the ssh:// form):
        those only rewrite GitHub SSH addresses to GitHub HTTPS, so they are allowed. Anything else is not."""
        out = self._git(["config", "--get-regexp",
                         r"^(remote\.origin\.(push|pushurl|mirror)|url\..*\.(insteadof|pushinsteadof)|"
                         r"core\.(hookspath|fsmonitor|sshcommand)|diff\.external|.*\.textconv|include\..*|"
                         r"includeif\..*)$"], where)
        if out is None:
            probe = self._git(["rev-parse", "--git-dir"], where)
            return "" if probe is not None else None      # no matches is exit 1: fine if it's a repo
        benign = re.compile(r"^url\.https://github\.com/\.insteadof (git@github\.com:|ssh://git@github\.com/)$")
        return "\n".join(l for l in out.splitlines() if l.strip() and not benign.match(l.strip()))

    def same_repo(self, path):
        """True if `path` is inside a checkout or worktree of the project's own repository."""
        mine = self._git(["rev-parse", "--path-format=absolute", "--git-common-dir"], self.project_dir)
        theirs = self._git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path)
        return bool(mine and theirs) and os.path.realpath(mine.strip()) == os.path.realpath(theirs.strip())

    def main_fetch_age(self, path):
        """Seconds since this checkout (or the repository) last fetched main; None if it never did."""
        ages = []
        for flag in ("--git-dir", "--git-common-dir"):
            out = self._git(["rev-parse", "--path-format=absolute", flag], path)
            if not out:
                continue
            fetch_head = Path(out.strip()) / "FETCH_HEAD"
            try:
                if "branch 'main' of" in fetch_head.read_text(encoding="utf-8", errors="replace"):
                    ages.append(time.time() - fetch_head.stat().st_mtime)
            except OSError:
                continue
        return min(ages) if ages else None

    def blob(self, path, ref, rel):
        """The object id of `rel` at `ref` ('' if it isn't there)."""
        out = self._git(["rev-parse", "-q", "--verify", f"{ref}:{rel}"], path)
        return out.strip() if out else ""

    def origin_url(self, where):
        out = self._git(["remote", "get-url", "--push", "origin"], where)
        return out.strip() if out else ""

    def github_prs(self, path):
        """GitHub's list of this repo's PRs, open and closed (None on error)."""
        repo = self.repo(path)
        if not repo:
            return None
        try:
            proc = subprocess.run(["gh", "api", f"repos/{repo}/pulls", "-X", "GET", "-f", "state=all",
                                   "-f", "per_page=100"], capture_output=True, text=True, timeout=20,
                                  cwd=self.toplevel(path) or None)
            prs = json.loads(proc.stdout) if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return prs if isinstance(prs, list) else None

    def supply_source_ok(self, path, number):
        """GitHub confirms comment `number` is on this repo and is a checker note (marked) or a requester's."""
        repo = self.repo(path)
        if not repo or not str(number).isdigit():
            return False
        try:
            proc = subprocess.run(["gh", "api", f"repos/{repo}/issues/comments/{int(number)}"], capture_output=True,
                                  text=True, timeout=20, cwd=self.toplevel(path) or None)
            comment = json.loads(proc.stdout) if proc.returncode == 0 else {}
        except (OSError, subprocess.SubprocessError, ValueError):
            return False
        if not isinstance(comment, dict) or f"repos/{repo}/issues/" not in str(comment.get("issue_url", "")):
            return False
        who = str((comment.get("user") or {}).get("login", "")).lower()
        requesters = {str(r).lower() for r in self.rules(path).get("requesters", [])}
        return str(comment.get("body", "")).lstrip().startswith(MARKER) or who in requesters

    def reserve_pr(self, path, head, cap):
        """Reserve tonight's slot for a PR from `head`, or say why not. Runs under a lock in the repository's git
        folder (no run can write there), so parallel PR calls are counted one at a time."""
        common = self._git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path)
        if not common:
            return "couldn't find this repository's git folder, so no new PR"
        folder = common.strip()
        try:
            with open(os.path.join(folder, "code-tower-prs.lock"), "a+", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._reserve_locked(path, head, cap, os.path.join(folder, "code-tower-prs.json"))
        except OSError as exc:
            return f"couldn't use the PR ledger ({type(exc).__name__}), so no new PR"

    def _reserve_locked(self, path, head, cap, ledger_path):
        prs = self.github_prs(path)
        if prs is None:
            return "couldn't read tonight's PRs on GitHub, so no new PR"
        now, row = datetime.now(timezone.utc), _row_id(head)
        github = []
        for pr in prs:
            ref = str(((pr.get("head") or {}) if isinstance(pr, dict) else {}).get("ref") or "")
            if ROW_BRANCH.match(ref):
                github.append((ref, _when(pr.get("created_at")), pr.get("state")))
        if any(_row_id(ref) == row and state == "open" for ref, _, state in github):
            return f"row {row} already has an open PR"
        try:
            ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ledger = []
        ledger = [e for e in ledger if isinstance(e, dict) and _when(e.get("at"))
                  and timedelta(0) <= now - _when(e["at"]) <= timedelta(hours=24)]
        tonight = [(ref, at) for ref, at, _ in github if at and timedelta(0) <= now - at <= timedelta(hours=24)]
        pending = [e for e in ledger
                   if not any(ref == e.get("head") and at >= _when(e["at"]) - timedelta(minutes=5) for ref, at in tonight)]
        if any(e.get("row") == row and now - _when(e["at"]) <= timedelta(minutes=10) for e in pending):
            return f"a PR for row {row} was allowed a moment ago; it isn't on GitHub yet, so no second one"
        count = len(tonight) + len(pending)
        if count >= cap:
            return f"tonight's draft-PR cap is reached ({count} of {cap} in the last 24 hours)"
        ledger.append({"head": head, "row": row, "at": now.isoformat()})
        tmp = ledger_path + ".tmp"
        Path(tmp).write_text(json.dumps(ledger), encoding="utf-8")
        os.replace(tmp, ledger_path)
        return ""

    def read(self, path):
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def _when(text):
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else None


SUPPLY_ID = re.compile(r"S[0-9]{1,20}")


def _known_row(env, path, rid):
    """A row in main's PLAN.md, or a supplied row (C1.5) whose id names a checker note or a requester's comment on
    this repo, which GitHub confirms."""
    return rid in env.plan_ids(path) or (bool(SUPPLY_ID.fullmatch(rid)) and env.supply_source_ok(path, rid[1:]))


def _row_id(branch):
    return re.sub(r"^(tower/|claude/tower-)", "", branch).upper()


def _switches(env, cwd):
    """(mode is on, rung) from main's rules; anything unreadable counts as off and rung 0."""
    rules = env.rules(cwd)
    try:
        rung = int(rules.get("rung", 0))
    except (TypeError, ValueError):
        rung = 0
    return rules.get("mode") == "on", rung


def _live(env, cwd):
    on, rung = _switches(env, cwd)
    return on and rung >= 1


def is_protected(rel, protected=PROTECTED):
    """True if a repo-relative path is a protected item, sits inside one, or contains one ('.', '.claude', 'tests')."""
    rel = os.path.normpath(rel.replace("\\", "/")).replace("\\", "/")
    if rel.startswith("../") or rel == "..":
        return False
    low = rel.lower()
    if low == ".":
        return True
    for item in protected:
        it = item.lower()
        base = it[:-1] if it.endswith("/") else it
        if low == base or (it.endswith("/") and low.startswith(it)) or base.startswith(low + "/"):
            return True
    return False


def _has_git_part(path):
    return ".git" in [p.lower() for p in Path(os.path.normpath(path)).parts]


def _full(path, cwd, env):
    raw = os.path.expanduser(path.strip("'\""))     # the file tools expand ~, so the guard does too
    full = raw if os.path.isabs(raw) else os.path.join(cwd or env.project_dir or ".", raw)
    try:
        return os.path.realpath(full)          # follow symlinks, so src/c -> ../checks still counts as checks
    except OSError:
        return os.path.normpath(full)


def _in_scope(full, env):
    """Inside this project's checkout (worktrees under .claude/worktrees/ included) or another worktree of the
    same repository."""
    project = os.path.realpath(env.project_dir or ".").rstrip("/")
    return (full + "/").startswith(project + "/") or env.same_repo(full)


def _write_refused(path, cwd, env, live=True):
    """Why a write to `path` is refused, or '' if it's allowed."""
    full = _full(path, cwd, env)
    if SECRET_PATH.search(full) or SECRET_PATH.search(path):
        return "secret or credential files are never written"
    if _has_git_part(path) or _has_git_part(full):
        return "nothing under a .git folder is written (git config, hooks and refs stay as they are)"
    top, here = env.toplevel(full), env.toplevel(cwd or env.project_dir)
    if not top or not here or os.path.realpath(top) != os.path.realpath(here) or not _in_scope(full, env):
        return ("files are written only in your own checkout of this repo (the tower: its runs/ct/; "
                "a worker: its own worktree)")
    rel = os.path.relpath(full, top).replace("\\", "/")
    if is_protected(rel, env.protected(top)) or AGENT_FILE.search(rel):
        return f"{rel} is part of the loop's own rules or agent instructions; the tower and workers never change it"
    if STARTUP_FILE.search(rel):
        return f"{rel}: pytest config, plugins and compiled files are never written by a run"
    tower = os.path.realpath(top) == os.path.realpath(env.project_dir or top)
    paths = TOWER_PATHS if (tower or not live) else WORKER_PATHS
    if (rel == "README.md" and not tower and live) or (rel.startswith(paths) and WRITABLE_EXT.search(rel)):
        return ""
    if not live:
        return f"{rel}: the rules say mode off or rung 0, so a run writes only runs/ct/"
    return f"{rel}: the tower writes only runs/ct/; workers write src/, tests/ and README.md in their own worktree"


def _checker_write(tool, path, cwd, env, live):
    """A checker only reads, except its verdict file: runs/ct/<row>-check.json in its own worktree. (Round 1,
    2026-11-08: an agent's final message didn't reach the tower, so verdicts and reports also go to a file.)"""
    no = (False, "a checker only reads, except its verdict: Write runs/ct/<row id>-check.json in your own worktree")
    if tool != "Write" or not path:
        return no
    why = _write_refused(path, cwd, env, live=False)
    full = _full(path, cwd, env)
    top = env.toplevel(full)
    if why or not top or os.path.realpath(top) == os.path.realpath(env.project_dir or top):
        return no
    rel = os.path.relpath(full, top).replace("\\", "/")
    return (True, "verdict") if re.fullmatch(r"runs/ct/[A-Za-z0-9_-]{1,20}-check\.json", rel) else no


def _read_refused(path, cwd, env):
    full = _full(path, cwd, env)
    if SECRET_PATH.search(full) or SECRET_PATH.search(path):
        return "reading or searching secret or credential files is blocked"
    if _has_git_part(path) or _has_git_part(full):
        return "git's own files (.git) aren't read directly; use the git command forms"
    sid = str(env.session_id or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", sid) and ".." not in path.split("/"):
        home = os.path.realpath(os.path.expanduser("~"))
        if (full + "/").startswith(f"{TOOL_RESULTS}{sid}/") or re.match(
                rf"^{re.escape(home)}/\.claude/projects/[^/]+/{re.escape(sid)}/tool-results/", full):
            return ""
    if not _in_scope(full, env):
        return "the tower reads only this repo's checkout and its worktrees (other files can hold secrets)"
    return ""


def _secretish(text):
    """True if `text` holds a known token format or a long run mixing upper case, lower case and digits."""
    if any(p.search(text) for p in SECRETS):
        return True
    for match in TOKENISH.finditer(SESSION_LINK.sub("", text)):
        token = match.group(0)
        if re.search(r"[a-z]", token) and re.search(r"[A-Z]", token) and re.search(r"[0-9]", token):
            return True
    return False


def _links_refused(text, repo):
    """Why `text` may not be posted for its links, or ''. Only this repo on GitHub, Notion and Claude Code's own
    pages: an image link elsewhere would carry data out when the page is viewed."""
    if len(text) > MAX_TEXT:
        return f"too long to post ({len(text)} characters; the limit is {MAX_TEXT})"
    if ENTITY.search(text):
        return "HTML character references (&#...; or &name;) aren't allowed in tower-written text"
    if IMAGE.search(text):
        return "images and embeds (![...], <img>, src=, [x]: link definitions) aren't allowed in tower-written text"
    text = html.unescape(text)
    low, own = text.lower(), f"https://github.com/{repo.lower()}"
    for match in SCHEME.finditer(low):
        rest = low[match.start():]
        if repo and rest.startswith(own) and (len(rest) == len(own) or rest[len(own)] in "/)]}>\"' \t\n.,;#?"):
            continue
        if any(rest.startswith(ok) for ok in LINKS_OK):
            continue
        return f"a link to {rest[:40]!r} isn't allowed (only this repo on GitHub, Notion and Claude Code)"
    if re.search(r"(?<![:/\w])//\w", text) or "\\\\" in text or re.search(r"\\/|/\\", text):
        return "protocol-relative or backslash links aren't allowed"
    return ""


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif value is not None:
        yield str(value)


# ---------------------------------------------------------------------------------------------- shell
# A Bash call runs only if the WHOLE command matches one of these forms. Each slot allows plain words, or simple
# quoted text, in a fixed character set, so bash can read the command only one way. There's no general parser to
# fool: no chains, no cd, no $, no globs, no other options. Read files with the Read, Grep and Glob tools.
ROW = r"(?:tower/|claude/tower-)[a-z0-9]{1,20}"
ID = r"[A-Z][A-Z0-9]{0,9}"
TEXT = r"[A-Za-z0-9 :,._()/+=-]"           # quoted text: no quotes, $, `, \, !, #, &, ;, |, <, >, globs or ~
QUOTED = rf"(?:'{TEXT}{{1,120}}'|\"{TEXT}{{1,120}}\")"
REL = r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}"
SRCTEST = r"(?:src|tests)/[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}"
TESTPATH = r"tests(?:/[A-Za-z0-9_]+){0,6}(?:\.py(?:::[A-Za-z0-9_]+){0,3})?/?"
REF = rf"(?:HEAD|origin/main|origin/{ROW}|{ROW})"
RANGE = rf"{REF}(?:\.\.\.?{REF})?"
JQ = r"(?:\.[A-Za-z0-9_.]{0,60}|'[A-Za-z0-9_ .,\[\]{}|:\"()=<>!?+*/-]{1,200}')"
GHVAL = rf"(?:[A-Za-z0-9_./:@+-]{{1,200}}|'{TEXT}{{0,200}}'|\"{TEXT}{{0,200}}\")"
SCRATCH = r"runs/ct/[A-Za-z0-9_-]{1,60}\.(?:json|txt)"
TAIL = r"(?: 2>&1| 2>/dev/null)?(?: \| (?:head|tail) (?:-n [0-9]{1,4}|-[0-9]{1,4}))?"
FORMS = (   # (name, form, may redirect stdout to runs/ct/<file>.json|txt, extra check)
    ("date", r"date -u(?: \+[%A-Za-z0-9:._TZ-]{1,40})?", False, ""),
    ("pwd", r"pwd", False, ""),
    ("version", r"(?:python3|git|gh) --version", False, ""),
    ("mkdir", r"mkdir -p runs/ct", False, ""),
    ("fetch", r"git fetch(?: (?:-q|--quiet|--prune|--unshallow))* origin(?: (?:-q|--quiet|--prune))*", False, ""),
    ("fetch row", rf"git fetch(?: (?:-q|--quiet))? origin refs/heads/(?P<frow>{ROW}):refs/remotes/origin/(?P=frow)",
     False, ""),
    ("ls-remote", rf"git ls-remote --heads origin(?: (?:refs/heads/)?{ROW})?", True, ""),
    ("status", r"git status(?: (?:--porcelain|--short|-s|-sb|-b|--branch))*", False, ""),
    ("switch", rf"git switch --detach origin/(?:main|{ROW})", False, ""),
    ("new branch", rf"git switch (?:--no-track )?-c {ROW} origin/main", False, ""),
    ("rev-parse", rf"git rev-parse(?: (?:--short|--abbrev-ref|--verify|-q)){{0,3}} {REF}|"
                  r"git rev-parse --(?:show-toplevel|is-shallow-repository)", False, ""),
    ("diff", rf"git diff(?: (?:--cached|--staged|--stat|--name-only|--name-status|--shortstat|--numstat|--check|"
             rf"-U[0-9]{{1,2}})){{0,6}}(?: {RANGE})?", False, ""),
    ("log", rf"git log(?: (?:--oneline|--stat|--name-only|--decorate|-n [0-9]{{1,3}}|-[0-9]{{1,3}})){{0,5}}"
            rf"(?: {RANGE})?", False, ""),
    ("show", rf"git show(?: (?:--stat|--name-only|--oneline)){{0,3}}(?: {REF})?", False, ""),
    ("branch", r"git branch(?: (?:-a|-r|-v|-vv|--list|--show-current)){0,3}", False, ""),
    ("worktree", r"git worktree list", False, ""),
    ("add", rf"git add (?:-A|--all|\.|(?:{SRCTEST}|README\.md)(?: (?:{SRCTEST}|README\.md)){{0,20}})", False, "paths"),
    ("commit", rf"git commit(?: -q)? -m {QUOTED}", False, "message"),
    ("push", rf"git push(?: -u)? origin HEAD:(?:refs/heads/)?{ROW}", False, "push"),
    ("run checks", rf"python3 -I checks/run_checks\.py(?: --only {ID})?(?: --strict)?", True, ""),
    ("check", r"python3 -I checks/[a-z0-9_]{1,40}\.py", False, ""),
    ("select", r"python3 -I tower/select_rows\.py(?: --returns runs/ct/returns\.json)?"
               r"(?: --proposals runs/ct/proposals\.json)?", True, ""),
    ("pytest", rf"python3 -I -m pytest(?: --version| (?:-q|-x|-v|-vv|-rA|--tb=(?:short|line|no)|{TESTPATH})){{0,8}}", False, ""),
    ("ls", rf"ls(?: -[al1AFh]{{1,5}}){{0,2}}(?: {REL}){{0,10}}", False, "read"),
    ("read", rf"(?:cat|wc -l|(?:head|tail) (?:-n [0-9]{{1,4}}|-[0-9]{{1,4}})) {REL}(?: {REL}){{0,10}}", False, "read"),
    ("gh auth", r"gh auth status", False, ""),
    ("gh api", r"gh api repos/<REPO>(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,100}){0,8}"
               rf"(?: (?:--paginate|-X (?:GET|POST)|-[fF] [a-z_]{{1,30}}={GHVAL}|--jq {JQ})){{0,12}}", True, "gh"),
)
WRITE_FORMS = {"new branch", "add", "commit", "push"}     # off with mode off or rung 0
NOT_A_FORM = ("not one of the tower's command forms. Run ONE command per call, exactly as the skill writes it: no "
              "&&, ;, cd, $, ~, globs, heredocs or extra options. Read and search files with the Read, Grep and "
              "Glob tools; write them with Edit and Write.")


def _form(cmd, repo):
    """(name, check, command part, redirect target) of the form `cmd` matches, or None."""
    for name, form, may_redirect, check in FORMS:
        pattern = rf"(?P<cmd>{form.replace('<REPO>', re.escape(repo))})"
        if may_redirect:
            pattern += rf"(?: > (?P<redir>{SCRATCH}))?"
        match = re.fullmatch(pattern + rf"(?P<tail>{TAIL})", cmd)
        if match:
            return name, check, match.group("cmd"), match.groupdict().get("redir")
    return None


def _stale(env, cwd):
    """Why main isn't fresh enough for an outward action, or ''."""
    age = env.main_fetch_age(cwd)
    if age is None or age > FRESH_SECONDS:
        return ("run `git fetch origin` first (a row-branch fetch doesn't count): pushes, PRs, comments and new "
                "workers need main fetched in the last 10 minutes, so Adam's switches on GitHub reach a running night")
    return ""


def _switch_refused(toks, cwd, env):
    target = toks[-1]
    tower = os.path.realpath(env.toplevel(cwd) or cwd or ".") == os.path.realpath(env.project_dir or ".")
    if tower and target != "origin/main":
        return ("the tower's own checkout stays on origin/main; workers and checkers switch to row branches in "
                "their own worktrees")
    ref = "refs/remotes/" + target
    mine, theirs = env.blob(cwd, MAIN, ".claude/settings.json"), env.blob(cwd, ref, ".claude/settings.json")
    if not mine or mine != theirs:
        return (f"{target} has a different .claude/settings.json from main's (Claude Code would reload hooks from "
                "it), so this checkout doesn't switch to it")
    return ""


def _shell_read_refused(toks, cwd, env):
    args = toks[1:]
    if toks[0] in ("head", "tail"):
        args = args[2:] if args[:1] == ["-n"] else args[1:]
    elif toks[0] == "wc":
        args = args[1:]
    top = env.toplevel(cwd or env.project_dir)
    for arg in (a for a in args if not a.startswith("-")):
        if ".." in arg.split("/"):
            return "no .. in paths"
        why = _read_refused(arg, cwd, env)
        if why:
            return why
        if not top or not (_full(arg, cwd, env) + "/").startswith(top.rstrip("/") + "/"):
            return "the shell reads only files inside this checkout"
    return ""


def _bash(cmd, cwd, env, live=True):
    cmd = cmd.strip()
    if not cmd or len(cmd) > 800:
        return block("empty or over-long command")
    repo = env.repo(cwd)
    if not repo:
        return block("couldn't read github_repo from main's rules/project.json")
    found = _form(cmd, repo)
    if not found:
        return block(NOT_A_FORM)
    name, check, part, redirect = found
    if name in WRITE_FORMS and not live:
        return block("the rules say mode off or rung 0: no commits or pushes")
    if name in WRITE_FORMS and env.agent_type == "checker":
        return block("a checker only reads: no branches, commits or pushes")
    toks = shlex.split(part)          # exact: the forms allow only plain words and simple quoted text
    if name == "switch":
        why = _switch_refused(toks, cwd, env)
        if why:
            return block(why)
    if check == "read":
        why = _shell_read_refused(toks, cwd, env)
        if why:
            return block(why)
    elif check == "paths" and any(".." in t.split("/") for t in toks[2:]):
        return block("no .. in paths")
    elif check == "message" and (_secretish(toks[-1]) or _links_refused(toks[-1], repo)):
        return block("the commit message holds token-like text or a link that isn't this repo, Notion or Claude Code")
    elif check == "push":
        why = _stale(env, cwd)
        if why:
            return block(why)
        ok, why = _push(toks[2:], cwd, env)
        if not ok:
            return ok, why
    elif check == "gh":
        ok, why = _gh_cmd(toks[1:], cwd, env, live)
        if not ok:
            return ok, why
    if redirect:
        why = _write_refused(redirect, cwd, env, live=False)      # runs/ct/ only
        if why:
            return block(why)
    return True, name


def _push(rest, where, env):
    toks = list(rest)
    if toks and toks[0] in ("-u", "--set-upstream"):
        toks = toks[1:]
    if len(toks) != 2 or toks[0] != "origin" or not toks[1].startswith("HEAD:"):
        return block("git push is allowed only as: git push [-u] origin HEAD:refs/heads/<row branch>")
    dst = toks[1][len("HEAD:"):]
    dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
    prefix = str(env.rules(where).get("row_branch_prefix", "tower/"))
    if not ROW_BRANCH.match(dst) or not dst.startswith(prefix):
        return block(f"pushing '{dst}' is blocked: only row branches named {prefix}<row>")
    if not _known_row(env, where, _row_id(dst)):
        return block(f"'{dst}' isn't the branch of a row in main's PLAN.md or of a supplied row GitHub confirms")
    redirects = env.push_redirects(where)
    if redirects is None or redirects:
        return block("git config here could redirect a push (or couldn't be read); push blocked")
    url, repo = env.origin_url(where), env.repo(where)
    if not url or not repo or not re.fullmatch(
            rf"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/){re.escape(repo)}(?:\.git)?/?", url, re.I):
        return block("origin isn't https://github.com/<this repo>; push blocked")
    changed = env.diff_names(where, "HEAD")
    if changed is None:
        return block("couldn't compare this branch with origin/main, so the push is blocked (in a shallow clone, run "
                     "git fetch --unshallow origin first)")
    protected = env.protected(where)
    touched = [f for f in changed if is_protected(f, protected) or AGENT_FILE.search(f) or STARTUP_FILE.search(f)]
    if touched:
        return block("this branch changes the loop's own rules, agent instructions or startup files ("
                     + ", ".join(touched[:5]) + "); push blocked")
    return True, "push"


def _gh_cmd(args, cwd, env, live=True):
    if args == ["auth", "status"]:
        return True, "gh auth status"
    if len(args) < 2 or args[0] != "api":
        return block("only `gh auth status` and `gh api` REST calls are allowed (other gh commands use GraphQL, "
                     "which Claude Code sessions block, and some merge or approve)")
    path, opts = args[1], args[2:]
    match = re.fullmatch(r"repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(/[A-Za-z0-9_./-]*)?(\?[A-Za-z0-9_=&,.-]*)?",
                         path)
    repo = env.repo(cwd)
    if not match or not repo or f"{match.group(1)}/{match.group(2)}".lower() != repo.lower():
        return block(f"gh api is limited to repos/{repo or '<this repo>'}/... routes")
    route = match.group(3) or ""
    pairs, i = [], 0
    while i < len(opts):
        flag = opts[i]
        if flag == "--paginate":
            pairs.append((flag, None))
            i += 1
        elif flag in ("-X", "--jq", "-f", "-F") and i + 1 < len(opts):
            pairs.append((flag, opts[i + 1]))
            i += 2
        else:
            return block(f"gh api option '{flag}' isn't allowed (write it as separate words: -X POST, -f key=value)")
    for flag, value in pairs:
        if flag == "--jq" and re.search(r"\benv\b|\$ENV|\binput\b|\$__loc__|\binputs\b|\bdebug\b|\bstderr\b|"
                                        r"\bhalt|\bimport\b|\binclude\b|\binput_filename\b", value):
            return block("that --jq filter isn't allowed")
    methods = [v.upper() for f, v in pairs if f == "-X"]
    fields = [(f, v) for f, v in pairs if f in ("-f", "-F")]
    if len(methods) > 1:
        return block("gh api: one -X only")
    method = methods[0] if methods else ("POST" if fields else "GET")
    if method == "GET":
        if any(f != "-f" or v.split("=", 1)[0] not in GH_GET_FIELDS for f, v in fields):
            return block("gh api reads may only add -f state= / per_page= / page= / sort= / direction=")
        return True, "gh api read"
    if method != "POST" or match.group(4) or "/ccr/" in route or re.search(r"/merges?(/|$)", route):
        return block(f"gh api {method} {route or '/'} is blocked")
    if not live:
        return block("the rules say mode off or rung 0: no PRs or comments")
    if env.agent_type == "checker":
        return block("a checker only reads: no PRs or comments")
    why = _stale(env, cwd)
    if why:
        return block(why)
    values = {}
    for flag, value in fields:
        if "=" not in value:
            return block("gh api fields must be key=value")
        key, val = value.split("=", 1)
        if key in values:
            return block(f"gh api field '{key}' given twice")
        values[key] = (flag, val)
    if route == "/pulls":
        ok = (set(values) == {"title", "head", "base", "draft", "body"} and values["title"][0] == "-f"
              and values["head"][0] == "-f" and ROW_BRANCH.match(values["head"][1])
              and values["base"] == ("-f", "main") and values["draft"] == ("-F", "true") and values["body"][0] == "-F")
        if not ok:
            return block("a PR must be exactly: -X POST -f title=… -f head=<row branch> -f base=main -F draft=true "
                         "-F body=@runs/ct/<file>.md")
        head = values["head"][1]
        prefix = str(env.rules(cwd).get("row_branch_prefix", "tower/"))
        if not head.startswith(prefix) or not _known_row(env, cwd, _row_id(head)):
            return block(f"a PR's head must be {prefix}<row> for a row in main's PLAN.md or a confirmed supplied row")
        if _secretish(values["title"][1]) or _links_refused(values["title"][1], env.repo(cwd)):
            return block("the PR title holds token-like text or a link that isn't this repo, Notion or Claude Code")
        okb, why = _body_ok(values["body"][1], cwd, env, comment=False)
        if not okb:
            return okb, why
        cap = int((env.rules(cwd).get("caps") or {}).get("draft_prs_per_night", 0))
        why = env.reserve_pr(cwd, head, cap)
        return block(why) if why else (True, "draft PR")
    if re.fullmatch(r"/issues/[0-9]+/comments", route):
        if set(values) != {"body"} or values["body"][0] != "-F":
            return block("a comment must be exactly: -X POST -F body=@runs/ct/<file>.md")
        return _body_ok(values["body"][1], cwd, env, comment=True)
    return block(f"gh api POST {route} is blocked: reads, draft PRs and comments only")


def _body_ok(value, cwd, env, comment):
    path = value[1:] if value.startswith("@") else ""
    if not path or not BODY_FILE.match(path):
        return block("the body must be a file under runs/ct/, passed as -F body=@runs/ct/<file>.md")
    text = env.read(path if os.path.isabs(path) else os.path.join(cwd or ".", path))
    if text is None:
        return block("couldn't read the body file")
    if _secretish(text):
        return block("the body holds secret-like or token-like text")
    why = _links_refused(text, env.repo(cwd))
    if why:
        return block(f"the body: {why}")
    if comment and text.lstrip().splitlines()[:1] != [MARKER]:
        return block(f"a tower comment must start with the line {MARKER}")
    return True, "gh api write"


# ---------------------------------------------------------------------------------------------- tools
def _mcp(tool, inp, env):
    parts = tool.split("__")
    server = parts[1].lower() if len(parts) >= 3 else ""
    name = parts[-1]
    bare = re.sub(r"^notion-", "", name) if "notion" in server else name
    if "notion" in server:
        if bare in ("fetch", "search", "query-data-sources"):
            return True, "Notion read"
        if bare == "create-pages":
            parent = inp.get("parent")
            ok = (isinstance(parent, dict) and set(parent) <= {"type", "data_source_id"}
                  and parent.get("type", "data_source_id") == "data_source_id"
                  and str(parent.get("data_source_id", "")).replace("collection://", "") == COORD_DS)
            if not ok:
                return block("Notion pages may be created only in the Coordination Entries data source")
            if not isinstance(inp.get("pages"), list) or len(inp["pages"]) != 1:
                return block("one Coordination Entry per call")
            page = inp["pages"][0]
            if not isinstance(page, dict) or {"cover", "icon"} & set(page) or {"cover", "icon"} & set(inp):
                return block("a Coordination Entry has no cover or icon (they would load an outside image)")
            text = "\n".join(_strings(inp.get("pages")))
            if _secretish(text):
                return block("the Coordination Entry holds secret-like or token-like text")
            why = _links_refused(text, env.repo(env.project_dir))
            if why:
                return block(f"the Coordination Entry: {why}")
            return True, "Coordination Entry"
        return block(f"Notion '{name}' is blocked: the tower only reads and writes one Coordination Entry")
    return block(f"'{tool}' is blocked: the tower reads Notion and writes one Coordination Entry; no other apps")


def decide(tool, inp, cwd, env=None):
    """Return (allowed, reason)."""
    env = env or Env(os.environ.get("CLAUDE_PROJECT_DIR", cwd or ""))
    env._cache = {}                    # git answers are cached within one decision only
    inp = inp or {}
    if tool.startswith("mcp__"):
        return _mcp(tool, inp, env)
    if tool not in BUILTIN_TOOLS:
        return block(f"'{tool}' isn't on the tower's tool list (no web, no other apps)")
    if tool in ("Read", "Grep", "Glob", "LS"):
        for key in ("file_path", "path", "notebook_path"):
            value = str(inp.get(key) or "")
            if value:
                why = _read_refused(value, cwd, env)
                if why:
                    return block(why)
        if tool in ("Grep", "Glob", "LS"):             # searches stay inside this repo's checkout
            where = _full(str(inp.get("path") or cwd or env.project_dir or "."), cwd, env)
            if not env.toplevel(where) or not _in_scope(where, env):
                return block("search only inside this repo's checkout")
        if tool == "Grep":
            top = env.toplevel(where)
            rel = os.path.relpath(where, top).replace("\\", "/") if top else ".."
            if not any(rel == r or rel.startswith(r + "/") for r in GREP_ROOTS):
                return block("give Grep a path inside src, tests, checks, tower, rules or runs/ct (or one of "
                             "README.md, PLAN.md, CLAUDE.md)")
        if tool == "Grep" and inp.get("glob") and not SAFE_GREP_GLOB.fullmatch(str(inp["glob"])):
            return block("Grep's glob may only pick files by extension, such as *.py or src/**/*.md")
        if tool == "Grep" and inp.get("type") and not re.fullmatch(r"[a-z]{1,12}", str(inp["type"])):
            return block("Grep's type must be a plain file type such as py")
        if tool == "Glob":
            value = str(inp.get("pattern") or "")
            if (SECRET_PATH.search(value) or re.search(r"(^|/)\.(env|ssh|aws|config|claude|credentials|git/)", value)
                    or value.startswith(("/", "~")) or ".." in value.split("/")):
                return block("search patterns stay inside the checkout and away from secret files")
        return True, "read"
    live = _live(env, cwd)
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if env.agent_type == "checker":
            return _checker_write(tool, path, cwd, env, live)
        why = _write_refused(path, cwd, env, live) if path else "no path"
        return (False, why) if why else (True, "file")
    if tool == "Bash":
        return _bash(str(inp.get("command", "")), cwd, env, live)
    if tool == "Skill" and str(inp.get("skill", "")) != "code-tower":
        return block("the tower loads only its own skill, code-tower")
    if tool in ("Agent", "Task"):
        if not live:
            return block("the rules say mode off or rung 0: no workers or checkers")
        if env.agent_type == "checker":
            return block("a checker only reads; it starts no agents")
        why = _stale(env, cwd)
        if why:
            return block(why)
        if inp.get("subagent_type") not in AGENTS or inp.get("isolation") != "worktree" or set(inp) - AGENT_KEYS:
            return block("start only a worker or a checker, with isolation: worktree and no other options "
                         "(description, prompt and run_in_background are fine)")
    return True, "built-in tool"


def main():
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook input is not an object")
        inp = event.get("tool_input") or {}
        if not isinstance(inp, dict):
            raise ValueError("tool_input is not an object")
        cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        env = Env(os.environ.get("CLAUDE_PROJECT_DIR") or cwd, str(event.get("session_id") or ""),
                  str(event.get("agent_type") or ""))
        ok, why = decide(str(event.get("tool_name", "")), inp, cwd, env)
    except Exception as exc:  # noqa: BLE001 - any failure blocks
        print(f"Blocked by .claude/hooks/guard.py: guard error ({type(exc).__name__}: {exc}); blocking to be safe",
              file=sys.stderr)
        sys.exit(2)
    if not ok:
        print(f"Blocked by .claude/hooks/guard.py: {why}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()

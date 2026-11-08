"""PreToolUse guard for a Code Tower repo. V0.4: exact command forms, after three independent checks (2026-11-07).

.claude/settings.json allows or denies tools by name; this hook looks inside each call. The hook command in
settings.json runs the copy committed on refs/remotes/origin/main when there is one, so editing the working file
changes nothing. Exit 0 = allow (the normal permission rules still apply) · exit 2 = block, reason on stderr.
Any error blocks. Hooks also run when a scheduled run auto-approves tools.

Principles: allow what the tower needs, in listed shapes; refuse everything else.
1. Tools: the listed built-in tools (Agent only as a worker or checker in its own worktree), Notion
   fetch/search/query, and one Notion page in the Coordination Entries data source. No other app, no web.
2. Shell: a command runs only if the whole command matches one of FORMS (below): one command per call, plain
   words and simple quoted text, no chains, cd, $, ~, globs or extra options.
3. Files are written only under src/, tests/ (not tests/fence/), README.md and runs/ct/ in a git checkout.
   Never the loop's own rules (PROTECTED), anything under .git, or anything outside a checkout.
4. git push only as `git push [-u] origin HEAD:[refs/heads/]<row branch>` for a row in PLAN.md, refused if the
   branch changes PROTECTED, if git config could redirect it, or if origin isn't this repo. gh only as
   `gh auth status`, REST reads, one draft-PR shape (within tonight's cap, counted from GitHub) and one comment
   shape on this repo.
5. No secrets: credential files, /proc, home dotfiles, .git internals and secret-like text in bodies and entries
   are refused; a Coordination Entry may link only to this repo on GitHub and to Notion.
Known gap (disclosed in PR #1): code a worker writes and runs (a test, say) can do anything the session's own
credentials allow. The PR guard rejects such code in src/ before merge; only GitHub branch protection (a public
repo or a paid plan) closes the gap fully.
"""
import json
import os
import re
import shlex
import subprocess
import sys
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
WRITABLE = ("src/", "tests/", "runs/ct/")          # plus README.md; never PROTECTED (tests/fence/ is protected)
WRITABLE_EXT = re.compile(r"\.(py|md|txt|json|csv)$")
# Files that change how Python or pytest starts (config, plugins, compiled code): never written by a run.
STARTUP_FILE = re.compile(r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml|sitecustomize\.py|"
                          r"usercustomize\.py|__pycache__/.*|[^/]+\.(pth|pyc|so|pyd))$", re.I)
PLAN_BLOCK = re.compile(r"```json exit-checks[ \t]*\n(.*?)\n```", re.S)
AGENTS = {"worker", "checker"}
LINK = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>)\]]*|(?<![:/A-Za-z0-9])//[A-Za-z0-9]", re.I)
MARKER = "<!-- code-tower -->"
GH_GET_FIELDS = {"state", "per_page", "page", "sort", "direction"}
BUILTIN_TOOLS = {"Read", "Grep", "Glob", "LS", "Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "BashOutput",
                 "KillShell", "KillBash", "Agent", "Task", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                 "TaskOutput", "TaskStop", "TodoWrite", "SendMessage", "Skill", "ToolSearch", "ExitPlanMode",
                 "EnterPlanMode", "AskUserQuestion"}      # not Monitor or SlashCommand: they can run shell commands
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
SECRET_PATH = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|[^/]*\.env|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*|id_ed25519[^/]*|credentials[^/]*|"
    r"\.credentials\.json|\.claude\.json|settings\.local\.json|\.git-credentials|\.netrc|\.npmrc|\.pypirc|"
    r"hosts\.yml)$|(^|/)(\.ssh|\.aws|\.docker|\.gnupg|\.config/gh)(/|$)|^/proc(/|$)|^/sys(/|$)", re.I)
def block(why):
    return False, why


# ---------------------------------------------------------------------------------------------- environment
class Env:
    """Git and file lookups. Tests replace this with a stub."""

    def __init__(self, project_dir=""):
        self.project_dir = project_dir

    @staticmethod
    def _git(args, path):
        p = Path(path) if path else None
        while p is not None and not p.exists() and p != p.parent:
            p = p.parent
        if p is None or not p.exists():
            return None
        where = str(p if p.is_dir() else p.parent)
        try:
            proc = subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

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
            return json.loads(self.main_file(top, "rules/project.json") or "{}") if top else {}
        except ValueError:
            return {}

    def repo(self, path):
        return str(self.rules(path).get("github_repo", ""))

    def protected(self, path):
        return PROTECTED + tuple(str(p) for p in self.rules(path).get("tamper_paths", ()))

    def diff_names(self, where, src):
        out = self._git(["diff", "--name-only", "--no-renames", f"{MAIN}...{src}"], where)
        return None if out is None else [l.strip() for l in out.splitlines() if l.strip()]

    def push_redirects(self, where):
        """Git config that could send a push somewhere else ('' if none; None if git can't answer).
        Claude Code's cloud sessions inject `url.https://github.com/.insteadof git@github.com:` (and the ssh:// form):
        those only rewrite GitHub SSH addresses to GitHub HTTPS, so they are allowed. Anything else is not."""
        out = self._git(["config", "--get-regexp",
                         r"^(remote\.origin\.(push|pushurl|mirror)|url\..*\.(insteadof|pushinsteadof)|core\.(hookspath|fsmonitor|sshcommand)|"
                         r"diff\.external|.*\.textconv|include\..*|includeif\..*)$"],
                        where)
        if out is None:
            probe = self._git(["rev-parse", "--git-dir"], where)
            return "" if probe is not None else None      # no matches is exit 1: fine if it's a repo
        benign = re.compile(r"^url\.https://github\.com/\.insteadof (git@github\.com:|ssh://git@github\.com/)$")
        return "\n".join(l for l in out.splitlines() if l.strip() and not benign.match(l.strip()))

    def plan_ids(self, path):
        """Row IDs in main's PLAN.md exit-check block (empty if it can't be read)."""
        top = self.toplevel(path) or self.project_dir
        match = PLAN_BLOCK.search(self.main_file(top, "PLAN.md") if top else "")
        try:
            return {str(c.get("id", "")) for c in json.loads(match.group(1)).get("checks", [])} if match else set()
        except (ValueError, AttributeError):
            return set()

    def row_prs_last_24h(self, path):
        """Row PRs opened on GitHub in the last 24 hours, counted by code from GitHub's own list (None on error)."""
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
        if not isinstance(prs, list):
            return None
        now, count = datetime.now(timezone.utc), 0
        for pr in prs:
            try:
                head = str((pr.get("head") or {}).get("ref") or "")
                when = datetime.fromisoformat(str(pr.get("created_at", "")).replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                continue
            if ROW_BRANCH.match(head) and timedelta(0) <= now - when <= timedelta(hours=24):
                count += 1
        return count

    def origin_url(self, where):
        out = self._git(["remote", "get-url", "--push", "origin"], where)
        return out.strip() if out else ""

    def read(self, path):
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


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


def _write_refused(path, cwd, env):
    """Why a write to `path` is refused, or '' if it's allowed."""
    full = _full(path, cwd, env)
    if SECRET_PATH.search(full) or SECRET_PATH.search(path):
        return "secret or credential files are never written"
    if _has_git_part(path) or _has_git_part(full):
        return "nothing under a .git folder is written (git config, hooks and refs stay as they are)"
    top = env.toplevel(full)
    if not top:
        return "files are written only inside a git checkout (src/, tests/, README.md, runs/ct/)"
    rel = os.path.relpath(full, top).replace("\\", "/")
    if is_protected(rel, env.protected(top)):
        return f"{path} is part of the loop's own rules; the tower and workers never change it"
    if STARTUP_FILE.search(rel):
        return f"{rel}: pytest config, plugins and compiled files are never written by a run"
    if rel == "README.md" or (rel.startswith(WRITABLE) and WRITABLE_EXT.search(rel)):
        return ""
    return f"{rel}: workers write only src/, tests/ and README.md; the tower writes only runs/ct/"


def _read_refused(path, cwd, env):
    full = _full(path, cwd, env)
    if SECRET_PATH.search(full) or SECRET_PATH.search(path):
        return "reading or searching secret or credential files is blocked"
    if _has_git_part(path) or _has_git_part(full):
        return "git's own files (.git) aren't read directly; use the git command forms"
    home = os.path.expanduser("~").rstrip("/")
    if (full + "/").startswith(home + "/"):
        inside_project = env.project_dir and (full + "/").startswith(os.path.realpath(env.project_dir) + "/")
        if not inside_project and not env.toplevel(full):
            return "reading in the home folder outside a git checkout is blocked"
    return ""


# ---------------------------------------------------------------------------------------------- shell
# V0.4: a Bash call runs only if the WHOLE command matches one of these forms. Each slot allows plain words, or
# simple quoted text, in a fixed character set, so bash can read the command only one way. There's no general
# parser to fool: no chains, no cd, no $, no globs, no other options. Read files with the Read, Grep and Glob tools.
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
    ("fetch", r"git fetch(?: (?:-q|--quiet|--prune))* origin(?: (?:-q|--quiet|--prune))*", False, ""),
    ("ls-remote", rf"git ls-remote --heads origin(?: (?:refs/heads/)?{ROW})?", True, ""),
    ("status", r"git status(?: (?:--porcelain|--short|-s|-sb|-b|--branch))*", False, ""),
    ("switch", rf"git switch --detach origin/(?:main|{ROW})", False, ""),
    ("new branch", rf"git switch (?:--no-track )?-c {ROW} origin/main", False, ""),
    ("rev-parse", rf"git rev-parse(?: (?:--short|--abbrev-ref|--verify|-q)){{0,3}} {REF}|git rev-parse --show-toplevel",
     False, ""),
    ("diff", rf"git diff(?: (?:--cached|--staged|--stat|--name-only|--name-status|--shortstat|--numstat|--check|"
             rf"-U[0-9]{{1,2}})){{0,6}}(?: {RANGE})?", False, ""),
    ("log", rf"git log(?: (?:--oneline|--stat|--name-only|--decorate|-n [0-9]{{1,3}}|-[0-9]{{1,3}})){{0,5}}"
            rf"(?: {RANGE})?", False, ""),
    ("show", rf"git show(?: (?:--stat|--name-only|--oneline)){{0,3}}(?: {REF})?", False, ""),
    ("branch", r"git branch(?: (?:-a|-r|-v|-vv|--list|--show-current)){0,3}", False, ""),
    ("worktree", r"git worktree list", False, ""),
    ("add", rf"git add (?:-A|--all|\.|(?:{SRCTEST}|README\.md)(?: (?:{SRCTEST}|README\.md)){{0,20}})", False, "paths"),
    ("commit", rf"git commit(?: -q)? -m {QUOTED}", False, ""),
    ("push", rf"git push(?: -u)? origin HEAD:(?:refs/heads/)?{ROW}", False, "push"),
    ("run checks", rf"python3 -I checks/run_checks\.py(?: --only {ID})?(?: --strict)?", True, ""),
    ("check", r"python3 -I checks/[a-z0-9_]{1,40}\.py", False, ""),
    ("select", r"python3 -I tower/select_rows\.py(?: --returns runs/ct/returns\.json)?", True, ""),
    ("pytest", rf"python3 -I -m pytest(?: (?:-q|-x|-v|-vv|-rA|--tb=(?:short|line|no)|{TESTPATH})){{0,8}}", False, ""),
    ("pip", r"python3 -m pip install --quiet pytest", False, ""),
    ("ls", rf"ls(?: -[al1AFh]{{1,5}}){{0,2}}(?: {REL}){{0,10}}", False, "read"),
    ("read", rf"(?:cat|wc -l|(?:head|tail) (?:-n [0-9]{{1,4}}|-[0-9]{{1,4}})) {REL}(?: {REL}){{0,10}}", False, "read"),
    ("gh auth", r"gh auth status", False, ""),
    ("gh api", r"gh api repos/<REPO>(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,100}){0,8}"
               rf"(?: (?:--paginate|-X (?:GET|POST)|-[fF] [a-z_]{{1,30}}={GHVAL}|--jq {JQ})){{0,12}}", True, "gh"),
)
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
            return "the shell reads only files inside this checkout (use the Read tool for anything else)"
    return ""


def _bash(cmd, cwd, env):
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
    toks = shlex.split(part)          # exact: the forms allow only plain words and simple quoted text
    if check == "read":
        why = _shell_read_refused(toks, cwd, env)
        if why:
            return block(why)
    elif check == "paths" and any(".." in t.split("/") for t in toks[2:]):
        return block("no .. in paths")
    elif check == "push":
        ok, why = _push(toks[2:], cwd, env)
        if not ok:
            return ok, why
    elif check == "gh":
        ok, why = _gh_cmd(toks[1:], cwd, env)
        if not ok:
            return ok, why
    if redirect:
        why = _write_refused(redirect, cwd, env)
        if why:
            return block(why)
    return True, name


def _row_id(branch):
    return re.sub(r"^(tower/|claude/tower-)", "", branch).upper()


def _push(rest, where, env):
    toks = list(rest)
    if toks and toks[0] in ("-u", "--set-upstream"):
        toks = toks[1:]
    if len(toks) != 2 or toks[0] != "origin" or not toks[1].startswith("HEAD:"):
        return block("git push is allowed only as: git push [-u] origin HEAD:refs/heads/<row branch>")
    dst = toks[1][len("HEAD:"):]
    dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
    if not ROW_BRANCH.match(dst):
        return block(f"pushing '{dst}' is blocked: only row branches (tower/<id>)")
    if ROW_BRANCH.sub("", dst) or _row_id(dst) not in env.plan_ids(where):
        return block(f"'{dst}' isn't the branch of a row in main's PLAN.md")
    redirects = env.push_redirects(where)
    if redirects is None or redirects:
        return block("git config here could redirect a push (or couldn't be read); push blocked")
    url, repo = env.origin_url(where), env.repo(where)
    if not url or not repo or not re.search(r"/" + re.escape(repo) + r"(\.git)?/?$", url, re.I):
        return block("origin doesn't point at this repo; push blocked")
    changed = env.diff_names(where, "HEAD")
    if changed is None:
        return block("couldn't compare this branch with origin/main, so the push is blocked")
    protected = env.protected(where)
    touched = [f for f in changed if is_protected(f, protected)]
    if touched:
        return block("this branch changes the loop's own rules (" + ", ".join(touched[:5]) + "); push blocked")
    return True, "push"


def _gh_cmd(args, cwd, env):
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
        if flag == "--jq" and re.search(r"\benv\b|\$ENV|\binput\b|\$__loc__|\binputs\b|\bdebug\b|\bstderr\b", value):
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
        if _row_id(values["head"][1]) not in env.plan_ids(cwd):
            return block("a PR's head must be the branch of a row in main's PLAN.md")
        okb, why = _body_ok(values["body"][1], cwd, env, comment=False)
        if not okb:
            return okb, why
        cap = int((env.rules(cwd).get("caps") or {}).get("draft_prs_per_night", 0))
        opened = env.row_prs_last_24h(cwd)
        if opened is None:
            return block("couldn't count tonight's row PRs on GitHub, so no new PR")
        if opened >= cap:
            return block(f"tonight's draft-PR cap is reached ({opened} of {cap} in the last 24 hours)")
        return True, "draft PR"
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
    if any(p.search(text) for p in SECRETS):
        return block("the body contains secret-like text")
    if comment and text.lstrip().splitlines()[:1] != [MARKER]:
        return block(f"a tower comment must start with the line {MARKER}")
    return True, "gh api write"


# ---------------------------------------------------------------------------------------------- tools
def _link_ok(link, env):
    repo = env.repo(env.project_dir).lower()
    low = link.lower().rstrip(".,;")
    return bool(repo) and (low == f"https://github.com/{repo}" or low.startswith(f"https://github.com/{repo}/")
                           or re.match(r"^https://(app\.notion\.com|www\.notion\.so|notion\.so)/", low) is not None
                           or low.startswith("collection://"))


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
            text = json.dumps(inp, default=str, ensure_ascii=False)
            if any(p.search(text) for p in SECRETS):
                return block("the Coordination Entry contains secret-like text")
            if any(not _link_ok(link, env) for link in LINK.findall(text.replace("\\/", "/"))):
                return block("a Coordination Entry may link only to this repo on GitHub and to Notion pages")
            return True, "Coordination Entry"
        return block(f"Notion '{name}' is blocked: the tower only reads and writes one Coordination Entry")
    return block(f"'{tool}' is blocked: the tower reads Notion and writes one Coordination Entry; no other apps")


def decide(tool, inp, cwd, env=None):
    """Return (allowed, reason)."""
    env = env or Env(os.environ.get("CLAUDE_PROJECT_DIR", cwd or ""))
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
        if tool in ("Grep", "Glob", "LS"):             # searches stay inside a checkout
            where = _full(str(inp.get("path") or cwd or env.project_dir or "."), cwd, env)
            if not env.toplevel(where):
                return block("search only inside a git checkout (Read a single file elsewhere)")
            for value in (str(inp.get("pattern") or "") if tool == "Glob" else "", str(inp.get("glob") or "")):
                if value.startswith(("/", "~")) or ".." in value.split("/"):
                    return block("search patterns stay inside the checkout (no /, ~ or ..)")
        # Glob's "pattern" and Grep's "glob" are file patterns; Grep's "pattern" is a text regex, so it isn't checked.
        patterns = [str(inp.get("glob") or "")] + ([str(inp.get("pattern") or "")] if tool == "Glob" else [])
        for value in (v for v in patterns if v):
            if (SECRET_PATH.search(value) or re.search(r"(^|/)\.(env|ssh|aws|config|claude|credentials|git/)", value)
                    or value.startswith(("~", "/proc", "/sys"))):
                return block("reading or searching secret or credential files is blocked")
        return True, "read"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        why = _write_refused(path, cwd, env) if path else "no path"
        return (False, why) if why else (True, "file")
    if tool == "Bash":
        return _bash(str(inp.get("command", "")), cwd, env)
    if tool in ("Agent", "Task"):
        if inp.get("subagent_type") not in AGENTS or inp.get("isolation") != "worktree":
            return block("start only a worker or a checker, each with isolation: worktree")
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
        ok, why = decide(str(event.get("tool_name", "")), inp, cwd)
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

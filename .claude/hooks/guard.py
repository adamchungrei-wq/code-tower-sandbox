"""PreToolUse guard for a Code Tower repo. V0.3: allow-lists, after two independent audits (2026-11-07).

.claude/settings.json allows or denies tools by name; this hook looks inside each call. The hook command in
settings.json runs the copy committed on refs/remotes/origin/main when there is one, so editing the working file
changes nothing. Exit 0 = allow (the normal permission rules still apply) · exit 2 = block, reason on stderr.
Any error blocks. Hooks also run when a scheduled run auto-approves tools.

Principles: allow what the tower needs, in listed shapes; refuse everything else.
1. Tools: only the listed built-in tools, Notion fetch/search/query, and one Notion page in the Coordination
   Entries data source. Anything else, any other app, and the web are refused.
2. Shell: only listed command words. No `$`, backticks, `~`, globs in command words, inline-code runners,
   wrappers, environment printing or `export`. git and gh only in listed shapes (below).
3. The loop's own rules (PROTECTED), anything under a `.git` folder, and files outside a git checkout or /tmp
   are never written from a tower or worker run, on any branch.
4. git push only as `git push [-u] origin HEAD:[refs/heads/]<row branch>` (an explicit destination, which git
   never remaps), refused if the branch changes PROTECTED or if git config redirects pushes.
   gh only as `gh auth status`, REST reads, one draft-PR shape and one comment shape on this repo.
5. No secrets: credential files, /proc, home dotfiles and secret-like text in bodies and entries are refused.
Known gap (disclosed in PR #1): code a worker writes and runs (a test, say) can do anything the session's own
credentials allow. Only GitHub branch protection (a public repo or a paid plan) closes that.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

MAIN = "refs/remotes/origin/main"
COORD_DS = "eb8da925-cf1c-4689-af1d-830f77fc5e96"
PROTECTED = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/", ".claude/agents/",
             ".claude/skills/", ".claude/commands/", ".claude/CLAUDE.md", ".github/", ".gitignore", ".mcp.json",
             "rules/", "checks/", "tower/", "tests/fence/", "PLAN.md", "CLAUDE.md", "CLAUDE.local.md",
             "pyproject.toml")
ROW_BRANCH = re.compile(r"^(tower/|claude/tower-)[a-z0-9]+$")
BODY_FILE = re.compile(r"^(\./)?runs/ct/[A-Za-z0-9_.-]+\.md$|^/.+/runs/ct/[A-Za-z0-9_.-]+\.md$")
MARKER = "<!-- code-tower -->"
GH_GET_FIELDS = {"state", "per_page", "page", "sort", "direction"}
BUILTIN_TOOLS = {"Read", "Grep", "Glob", "LS", "Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "BashOutput",
                 "KillShell", "KillBash", "Agent", "Task", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                 "TaskOutput", "TaskStop", "TodoWrite", "SendMessage", "Skill", "ToolSearch", "ExitPlanMode",
                 "EnterPlanMode", "AskUserQuestion", "Monitor", "SlashCommand"}
SHELL_HEADS = {"date", "mkdir", "ls", "cat", "head", "tail", "wc", "grep", "diff", "cmp", "echo", "printf", "git",
               "gh", "python3", "python", "cd", "pwd", "true", "false", "sort", "uniq", "tr", "cut", "nl", "basename",
               "dirname", "realpath", "stat", "file", "which", "comm", "fold"}
READERS = {"ls", "cat", "head", "tail", "wc", "grep", "diff", "cmp", "sort", "uniq", "nl", "stat", "file", "comm",
           "realpath", "cut", "fold"}
GIT_SUBCOMMANDS = {"status", "diff", "log", "show", "add", "commit", "switch", "checkout", "restore", "fetch",
                   "ls-remote", "rev-parse", "branch", "worktree", "push", "rm", "mv", "merge-base",
                   "ls-files", "blame", "shortlog", "describe", "cat-file", "grep"}
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
PYTEST_ARG = re.compile(r"^(-q|-x|-v|-vv|--tb=(short|line|no)|tests(/[A-Za-z0-9_./-]*)?(::[A-Za-z0-9_]+)*)$")
PY_SHAPES = (
    re.compile(r"^python3? -I checks/[a-z0-9_]+\.py( --(only|plan|strict)( [A-Za-z0-9_./-]+)?)*$"),
    re.compile(r"^python3? -I tower/[a-z0-9_]+\.py( [A-Za-z0-9_./=-]+)*$"),
    re.compile(r"^python3? -m pip install --quiet pytest$"),
)
REDIRECTS = {">", ">>", "<", ">|", "&>", "&>>"}
MAX_TOKENS = 200


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
                         r"^(remote\.origin\.(push|pushurl|mirror)|url\..*\.(insteadof|pushinsteadof)|core\.hookspath)$"],
                        where)
        if out is None:
            probe = self._git(["rev-parse", "--git-dir"], where)
            return "" if probe is not None else None      # no matches is exit 1: fine if it's a repo
        benign = re.compile(r"^url\.https://github\.com/\.insteadof (git@github\.com:|ssh://git@github\.com/)$")
        return "\n".join(l for l in out.splitlines() if l.strip() and not benign.match(l.strip()))

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
    raw = path.strip("'\"")
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
        if (full + "/").startswith("/tmp/"):
            return ""
        return "writes outside a git checkout are refused (except /tmp)"
    if is_protected(os.path.relpath(full, top), env.protected(top)):
        return f"{path} is part of the loop's own rules; the tower and workers never change it"
    return ""


def _read_refused(path, cwd, env):
    full = _full(path, cwd, env)
    if SECRET_PATH.search(full) or SECRET_PATH.search(path):
        return "reading or searching secret or credential files is blocked"
    home = os.path.expanduser("~").rstrip("/")
    if (full + "/").startswith(home + "/"):
        inside_project = env.project_dir and (full + "/").startswith(os.path.realpath(env.project_dir) + "/")
        if not inside_project and not env.toplevel(full):
            return "reading in the home folder outside a git checkout is blocked"
    return ""


# ---------------------------------------------------------------------------------------------- shell
def _segments(cmd):
    """Split a shell command into simple commands, respecting quotes. None if it can't be parsed."""
    lexer = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        toks = list(lexer)
    except ValueError:
        return None
    segments, current = [], []
    for tok in toks:
        if tok and set(tok) <= set("|&;()"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _split_redirects(toks):
    args, targets, i = [], [], 0
    while i < len(toks):
        tok = toks[i]
        if tok in REDIRECTS:
            if i + 1 < len(toks):
                targets.append(toks[i + 1])
            i += 2
            continue
        if re.fullmatch(r"[0-9]+", tok) and i + 1 < len(toks) and toks[i + 1] in REDIRECTS:
            i += 1
            continue
        args.append(tok)
        i += 1
    return args, targets


def _git_cmd(args, cwd, env):
    where, i = cwd, 0
    if args[:1] == ["-C"]:
        if len(args) < 2:
            return block("git -C needs a folder")
        where, i = os.path.normpath(os.path.join(cwd or ".", args[1])), 2
    if i < len(args) and args[i].startswith("-"):
        return block(f"git option '{args[i]}' before the command is blocked (only one -C is allowed)")
    if i >= len(args):
        return True, "git"
    sub, rest = args[i], args[i + 1:]
    if sub not in GIT_SUBCOMMANDS:
        return block(f"git {sub} isn't on the tower's list")
    plain = [a for a in rest if not a.startswith("-")]
    if sub in ("fetch", "ls-remote"):
        if any(a not in ("origin", "--heads", "--prune", "-q", "--quiet") for a in rest):
            return block(f"git {sub} is allowed only as: git {sub} [--prune|--heads] origin")
        return True, "git"
    if sub == "push":
        return _push(rest, where, env)
    if sub == "branch":
        for name in plain:
            shadowing = name.lower().startswith(("origin/", "refs/", "remotes/", "heads/"))
            if shadowing or ("/" in name and not ROW_BRANCH.match(name)):
                return block("git branch may only name row branches (a branch called origin/... would shadow main)")
    if sub in ("switch", "checkout"):
        if any(a.lower().startswith(("refs/", "remotes/")) for a in plain):
            return block(f"git {sub}: use origin/<branch> or a row branch, not a raw ref")
        for flag in ("-c", "-C", "-b", "-B", "--orphan"):
            if flag in rest:
                pos = rest.index(flag)
                if pos + 1 >= len(rest) or not ROW_BRANCH.match(rest[pos + 1]):
                    return block(f"git {sub} may create only row branches (tower/<id>)")
    if sub == "worktree" and plain[:1] == ["add"]:
        target = plain[1] if len(plain) > 1 else ""
        if not target or not (_full(target, where, env) + "/").startswith("/tmp/"):
            return block("git worktree add: put the worktree under /tmp/ct-<row>")
    paths = []
    if sub == "checkout":
        paths = rest[rest.index("--") + 1:] if "--" in rest else [
            a for a in plain if os.path.exists(os.path.join(where or ".", a))]
    elif sub in ("restore", "rm", "mv"):
        paths = plain
    for p in paths:
        why = _write_refused(p, where, env)
        if why:
            return block(f"git {sub}: {why}")
    return True, "git"


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
    redirects = env.push_redirects(where)
    if redirects is None or redirects:
        return block("git config here could redirect a push (or couldn't be read); push blocked")
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
        return _body_ok(values["body"][1], cwd, env, comment=False)
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


def _bash(cmd, cwd, env):
    if re.search(r"[$`]", cmd):
        return block("no $, variables, substitutions or backticks in tower commands; write values out literally")
    if re.search(r"/dev/(tcp|udp)/", cmd):
        return block("network paths are blocked")
    segments = _segments(cmd)
    if segments is None:
        return block("couldn't parse this command safely; use plain quoting")
    if sum(len(s) for s in segments) > MAX_TOKENS:
        return block("command too long; split it up")
    here = cwd
    for toks in segments:
        if toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            return block("no variable assignments in tower commands")
        args, targets = _split_redirects(toks)
        if not args:
            return block("a redirect needs a command")
        head = args[0]
        if re.search(r"[*?\[\]{}~\\]", head) or "/" in head:
            return block(f"'{head}': run commands by their plain name")
        if head not in SHELL_HEADS:
            return block(f"'{head}' isn't on the tower's command list (use the Read, Grep, Glob, Edit and Write "
                         "tools for files)")
        for tok in args[1:] + targets:
            if tok.startswith("~"):
                return block("no ~ paths in tower commands")
        if head not in ("git", "gh"):
            plain_idx = [i for i, a in enumerate(args) if i > 0 and not a.startswith("-")]
            grep_pattern = plain_idx[0] if head == "grep" and plain_idx else None   # grep's regex may use [ * ?
            for i, tok in enumerate(args[1:], start=1):
                if i != grep_pattern and re.search(r"[*?\[]", tok):
                    return block("no glob patterns in tower commands (use the Glob tool)")
            if any(re.search(r"[*?\[]", t) for t in targets):
                return block("no glob patterns in tower commands (use the Glob tool)")
        for target in targets:
            if target in ("/dev/null",) or target.startswith("&"):
                continue
            why = _write_refused(target, here, env)
            if why:
                return block(why)
        if head == "cd":
            here = _full(args[1], here, env) if len(args) > 1 else here
            continue
        if head == "mkdir":
            for a in args[1:]:
                if not a.startswith("-"):
                    why = _write_refused(a, here, env)
                    if why:
                        return block(why)
        if head == "sort" and any(a.startswith("-o") or a == "--output" or a.startswith("--output=") for a in args):
            return block("sort -o is blocked")
        if head in READERS:
            for a in args[1:]:
                if not a.startswith("-"):
                    why = _read_refused(a, here, env)
                    if why:
                        return block(why)
        if head in ("python", "python3"):
            if args[1:3] == ["-I", "-m"] and args[3:4] == ["pytest"]:
                if not all(PYTEST_ARG.match(a) for a in args[4:]):
                    return block("pytest runs only with -q, -x, -v, --tb=… and paths under tests/")
            elif not any(p.match(" ".join(["python3"] + args[1:])) for p in PY_SHAPES):
                return block("python runs only as: python3 -I -m pytest …, python3 -I checks/<x>.py, "
                             "python3 -I tower/<x>.py, or python3 -m pip install --quiet pytest")
        if head == "git":
            ok, why = _git_cmd(args[1:], here, env)
            if not ok:
                return ok, why
        if head == "gh":
            ok, why = _gh_cmd(args[1:], here, env)
            if not ok:
                return ok, why
    return True, "bash"


# ---------------------------------------------------------------------------------------------- tools
def _mcp(tool, inp):
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
            if any(p.search(json.dumps(inp, default=str)) for p in SECRETS):
                return block("the Coordination Entry contains secret-like text")
            return True, "Coordination Entry"
        return block(f"Notion '{name}' is blocked: the tower only reads and writes one Coordination Entry")
    return block(f"'{tool}' is blocked: the tower reads Notion and writes one Coordination Entry; no other apps")


def decide(tool, inp, cwd, env=None):
    """Return (allowed, reason)."""
    env = env or Env(os.environ.get("CLAUDE_PROJECT_DIR", cwd or ""))
    inp = inp or {}
    if tool.startswith("mcp__"):
        return _mcp(tool, inp)
    if tool not in BUILTIN_TOOLS:
        return block(f"'{tool}' isn't on the tower's tool list (no web, no other apps)")
    if tool in ("Read", "Grep", "Glob", "LS"):
        for key in ("file_path", "path", "notebook_path"):
            value = str(inp.get(key) or "")
            if value:
                why = _read_refused(value, cwd, env)
                if why:
                    return block(why)
        # Glob's "pattern" and Grep's "glob" are file patterns; Grep's "pattern" is a text regex, so it isn't checked.
        patterns = [str(inp.get("glob") or "")] + ([str(inp.get("pattern") or "")] if tool == "Glob" else [])
        for value in (v for v in patterns if v):
            if (SECRET_PATH.search(value) or re.search(r"(^|/)\.(env|ssh|aws|config/gh|claude\.json)", value)
                    or value.startswith(("~", "/proc", "/sys"))):
                return block("reading or searching secret or credential files is blocked")
        return True, "read"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        why = _write_refused(path, cwd, env) if path else "no path"
        return (False, why) if why else (True, "file")
    if tool == "Bash":
        return _bash(str(inp.get("command", "")), cwd, env)
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

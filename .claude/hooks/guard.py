"""PreToolUse guard for a Code Tower repo. V0.2, hardened after the independent audit of 2026-11-07.

.claude/settings.json allows or denies tools by name; this hook looks inside each call. The hook command in
settings.json runs MAIN's committed copy of this file when main has one, so editing the working file changes
nothing. Exit 0 = allow (the normal permission rules still apply) · exit 2 = block, reason on stderr. Any
error blocks. Hooks also run when a scheduled run auto-approves tools, so this is the fence that holds at night.

Principles
1. The loop's own rules (PROTECTED) are never written from a tower or worker run, on any branch. Rules change
   only through a PR made outside the tower (chat 04, or Adam on GitHub) that Adam merges.
2. Anything that reaches GitHub or a git remote is allowed only in an exact shape:
     git push [-u] origin <row branch>        git push origin HEAD:[refs/heads/]<row branch>
     gh auth status
     gh api repos/<this repo>/<route> [--jq EXPR] [--paginate] [-X GET -f state=… -f per_page=…]    (read)
     gh api repos/<this repo>/pulls -X POST -f title=… -f head=<row branch> -f base=main -F draft=true
            -F body=@runs/ct/<file>.md [--jq EXPR]                                                  (draft PR)
     gh api repos/<this repo>/issues/<n>/comments -X POST -F body=@runs/ct/<file>.md [--jq EXPR] (comment)
   A push is also refused when the branch's diff from origin/main touches PROTECTED.
3. No secrets: secret and credential files are never read, searched, written, printed or posted.
4. No web, closed inputs: no curl/wget/ssh; MCP tools are denied except Notion fetch/search/query and creating
   one page in the Coordination Entries data source.
Known gap (disclosed in PR #1): code a worker writes and runs (a test, say) can still do anything the
session's own credentials allow. Only GitHub branch protection (public repo or a paid plan) closes that.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

COORD_DS = "eb8da925-cf1c-4689-af1d-830f77fc5e96"
PROTECTED = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/", ".claude/agents/",
             ".claude/skills/", ".claude/commands/", ".github/", ".git/", ".gitignore", "rules/", "checks/",
             "tower/", "tests/fence/", "PLAN.md", "CLAUDE.md", "pyproject.toml")
ROW_BRANCH = re.compile(r"^(tower/|claude/tower-)[a-z0-9]+$")
BODY_FILE = re.compile(r"^(\./)?runs/ct/[A-Za-z0-9_.-]+\.md$|^/.+/runs/ct/[A-Za-z0-9_.-]+\.md$")
GH_GET_FIELDS = {"state", "per_page", "page", "sort", "direction"}
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
    r"\.credentials\.json|settings\.local\.json|\.git-credentials|\.netrc|\.npmrc|\.pypirc|hosts\.yml)$"
    r"|(^|/)(\.ssh|\.aws|\.docker|\.gnupg|\.config/gh)(/|$)", re.I)
SECRET_IN_CMD = re.compile(
    r"(\.e[n?*\[]|\.pem\b|\.key\b|id_rsa|id_ed25519|credentials|\.netrc|\.npmrc|\.pypirc|\.ssh\b|\.aws\b|"
    r"\.docker\b|\.gnupg|\.config/gh|hosts\.yml|settings\.local\.json)", re.I)
RM_RF = re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|-r\s+-f|-f\s+-r)\b")
# Never allowed anywhere in a command, whatever the quoting.
RAW_BLOCK = (
    (re.compile(r"\bgh\s+pr\s+(merge|ready|review|close|edit)\b"), "gh pr merge / ready / review / close / edit"),
    (re.compile(r"\bgh\s+api\b[^\n]*(/merges?\b|/ccr/|\bgraphql\b)"), "GitHub merge, ready or GraphQL routes"),
    (re.compile(r"\bgit\b[^\n]*\bpush\b[^\n]*(--force|\s-f\b|--delete|\s-d\b|\s:\S|--mirror|--all|\bmain\b)"),
     "force, delete, mirror or main pushes"),
)
WEB_HEADS = {"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "rsync", "ftp", "socat",
             "lynx", "w3m", "aria2c", "http", "https"}
WRAPPERS = {"env", "printenv", "xargs", "eval", "exec", "sudo", "doas", "su", "command", "builtin", "nohup",
            "timeout", "nice", "stdbuf", "time", "watch", "bash", "sh", "zsh", "dash", "fish", "ksh", "busybox",
            "parallel", "script", "nsenter", "chroot", "source", "."}
WRITERS = {"tee", "mv", "cp", "rm", "rmdir", "truncate", "chmod", "chown", "touch", "ln", "install", "dd",
           "patch", "unzip", "tar", "cpio", "mkdir", "shred"}
GIT_BLOCKED = {"config", "remote", "send-pack", "update-ref", "symbolic-ref", "filter-branch", "filter-repo",
               "replace", "apply", "am", "submodule", "credential", "credential-store", "daemon", "http-push",
               "fast-import", "hook", "bundle", "archive", "request-pull", "notes"}
PY_SHAPES = (
    re.compile(r"^python3? -I -m pytest( .*)?$"),
    re.compile(r"^python3? -I checks/[a-z0-9_]+\.py( .*)?$"),
    re.compile(r"^python3? -I tower/[a-z0-9_]+\.py( .*)?$"),
    re.compile(r"^python3? -m pip install --quiet pytest$"),
)
REDIRECTS = {">", ">>", "<", "<<", "<<<", ">|", "&>", "&>>"}


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
        """A file as committed on origin/main (falls back to the working copy before main has it)."""
        out = self._git(["show", f"origin/main:{rel}"], top)
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
        out = self._git(["diff", "--name-only", "--no-renames", f"origin/main...{src}"], where)
        return None if out is None else [l.strip() for l in out.splitlines() if l.strip()]

    def read(self, path):
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def is_protected(rel, protected=PROTECTED):
    rel = os.path.normpath(rel.replace("\\", "/")).replace("\\", "/")
    if rel.startswith("../") or rel == "..":
        return False
    for item in protected:
        if item.endswith("/"):
            if rel == item[:-1] or rel.startswith(item):
                return True
        elif rel == item:
            return True
    return False


def _rel_in_repo(path, cwd, env):
    """(toplevel, repo-relative path) for a path, or ("", "") when it isn't inside a repo."""
    raw = path.strip("'\"")
    full = raw if os.path.isabs(raw) else os.path.join(cwd or env.project_dir or ".", raw)
    full = os.path.normpath(full)
    top = env.toplevel(full) or ""
    if not top:
        return "", ""
    return top, os.path.relpath(full, top)


def _protected_path(path, cwd, env):
    top, rel = _rel_in_repo(path, cwd, env)
    return bool(top) and is_protected(rel, env.protected(top))


# ---------------------------------------------------------------------------------------------- shell
def _segments(cmd):
    """Split a shell command into simple commands, respecting quotes. None if it can't be parsed."""
    lexer = shlex.shlex(cmd.replace("\n", " ; ").replace("`", " ; "), posix=True, punctuation_chars=True)
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
        if tok in REDIRECTS or re.fullmatch(r"[0-9]+", tok) and i + 1 < len(toks) and toks[i + 1] in REDIRECTS:
            if tok not in REDIRECTS:          # "2" in "2 > file": skip the fd number
                i += 1
                continue
            if i + 1 < len(toks):
                targets.append(toks[i + 1])
            i += 2
            continue
        args.append(tok)
        i += 1
    return args, targets


def _writes_protected(args, targets, cwd, env):
    if any(_protected_path(t, cwd, env) for t in targets if t != "/dev/null" and not t.startswith("&")):
        return True
    head, rest = os.path.basename(args[0]), args[1:]
    plain = [a for a in rest if not a.startswith("-")]
    if head == "dd":
        plain = [a.split("=", 1)[1] for a in rest if a.startswith("of=")]
    if head in WRITERS and any(_protected_path(a, cwd, env) for a in plain):
        return True
    if head in ("tar", "unzip", "cpio") and any(a.startswith("-C") or a == "-d" for a in rest):
        return any(_protected_path(a, cwd, env) for a in plain)
    if head in ("sed", "perl", "ruby") and any(a.startswith("-i") for a in rest):
        return any(_protected_path(a, cwd, env) for a in plain)
    return False


def _git_cmd(args, cwd, env):
    where, i = cwd, 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            where = os.path.normpath(os.path.join(cwd or ".", args[i + 1]))
            i += 2
            continue
        return block(f"git option '{args[i]}' before the command is blocked (only -C is allowed)")
    if i >= len(args):
        return True, "git"
    sub, rest = args[i], args[i + 1:]
    if sub in GIT_BLOCKED:
        return block(f"git {sub} is blocked")
    if sub == "reset" and any(a in ("--hard", "--merge", "--keep") for a in rest):
        return block("git reset --hard is blocked")
    if sub in ("fetch", "pull", "ls-remote"):
        positional = [a for a in rest if not a.startswith("-")]
        if any("://" in a or a.startswith("git@") or a.startswith("/") for a in rest) or (
                positional and positional[0] != "origin"):
            return block(f"git {sub} works only with origin")
        return True, "git"
    if sub == "push":
        return _push(rest, where, env)
    paths = []
    if sub == "checkout":
        paths = rest[rest.index("--") + 1:] if "--" in rest else [
            a for a in rest if not a.startswith("-") and os.path.exists(os.path.join(where or ".", a))]
    elif sub in ("restore", "rm", "mv"):
        paths = [a for a in rest if not a.startswith("-")]
    elif sub == "worktree" and rest[:1] == ["add"]:
        paths = [a for a in rest[1:] if not a.startswith("-")][:1]
    if any(_protected_path(p, where, env) for p in paths):
        return block(f"git {sub} on the loop's own rules is blocked")
    return True, "git"


def _push(rest, where, env):
    toks = list(rest)
    if toks and toks[0] in ("-u", "--set-upstream"):
        toks = toks[1:]
    if len(toks) != 2 or toks[0] != "origin":
        return block("git push is allowed only as: git push [-u] origin <row branch>, or "
                     "git push origin HEAD:<row branch>")
    ref = toks[1]
    if ref.startswith("HEAD:"):
        src, dst = "HEAD", ref[len("HEAD:"):]
    elif ":" in ref:
        return block("git push <src>:<dst> is allowed only as HEAD:<row branch>")
    else:
        src, dst = ref, ref
    dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
    if not ROW_BRANCH.match(dst):
        return block(f"pushing '{dst}' is blocked: only row branches (tower/<id>)")
    changed = env.diff_names(where, src)
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
        return _body_ok(values["body"][1], cwd, env)
    if re.fullmatch(r"/issues/[0-9]+/comments", route):
        if set(values) != {"body"} or values["body"][0] != "-F":
            return block("a comment must be exactly: -X POST -F body=@runs/ct/<file>.md")
        return _body_ok(values["body"][1], cwd, env)
    return block(f"gh api POST {route} is blocked: reads, draft PRs and comments only")


def _body_ok(value, cwd, env):
    path = value[1:] if value.startswith("@") else ""
    if not path or not BODY_FILE.match(path):
        return block("the body must be a file under runs/ct/, passed as -F body=@runs/ct/<file>.md")
    text = env.read(path if os.path.isabs(path) else os.path.join(cwd or ".", path))
    if text is None:
        return block("couldn't read the body file")
    if any(p.search(text) for p in SECRETS):
        return block("the body contains secret-like text")
    return True, "gh api write"


def _bash(cmd, cwd, env):
    if RM_RF.search(cmd):
        return block("rm -rf is blocked")
    if SECRET_IN_CMD.search(cmd):
        return block("commands that touch secret or credential files are blocked")
    for pattern, what in RAW_BLOCK:
        if pattern.search(cmd):
            return block(f"{what} are blocked")
    segments = _segments(cmd)
    if segments is None:
        if re.search(r"\b(gh|git)\b", cmd):
            return block("couldn't parse this command safely; run git and gh as simple commands")
        return True, "bash"
    for toks in segments:
        assignments = 0
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks, assignments = toks[1:], assignments + 1
        if not toks:
            continue
        args, targets = _split_redirects(toks)
        if not args:
            continue
        head = os.path.basename(args[0])
        names = [os.path.basename(t) for t in args]
        if head in WEB_HEADS:
            return block(f"{head} is blocked: no web access from a Code Tower repo")
        if head in WRAPPERS:
            return block(f"'{head}' is blocked: run commands directly")
        if head in ("set", "export", "declare", "typeset") and (len(args) == 1 or args[1] in ("-p", "-x")):
            return block("printing the environment is blocked")
        if ("git" in names[1:] or "gh" in names[1:]) and head not in ("git", "gh"):
            return block("git and gh must be the first word of a command")
        if head in ("git", "gh"):
            if assignments:
                return block("no variable assignments in front of git or gh")
            if any("$" in t for t in args):
                return block("no variables or substitutions inside git or gh commands")
            ok, why = (_git_cmd if head == "git" else _gh_cmd)(args[1:], cwd, env)
            if not ok:
                return ok, why
        if head in ("python", "python3") or re.fullmatch(r"python3(\.[0-9]+)?", head):
            if not any(p.match(" ".join([head] + args[1:])) for p in PY_SHAPES):
                return block("python runs only as: python3 -I -m pytest …, python3 -I checks/<x>.py, "
                             "python3 -I tower/<x>.py, or python3 -m pip install --quiet pytest")
        if head in ("pip", "pip3"):
            return block("pip is blocked (use: python3 -m pip install --quiet pytest)")
        if _writes_protected(args, targets, cwd, env):
            return block("shell writes to the loop's own rules are blocked")
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
            ok = (isinstance(parent, dict) and set(parent) == {"data_source_id"}
                  and str(parent["data_source_id"]).replace("collection://", "") == COORD_DS)
            if not ok:
                return block("Notion pages may be created only in the Coordination Entries data source")
            if any(p.search(json.dumps(inp, default=str)) for p in SECRETS):
                return block("the Coordination Entry contains secret-like text")
            return True, "Coordination Entry"
        return block(f"Notion '{name}' is blocked: the tower only reads and writes one Coordination Entry")
    return block(f"'{tool}' is blocked: the tower reads Notion and writes one Coordination Entry; no other apps")


def _outside_repo_home(path, env):
    """True for a path in the home folder that isn't inside the project or any git checkout (a worker's
    worktree may live outside the project folder). Credential files are blocked by name separately."""
    home = os.path.expanduser("~")
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(env.project_dir or ".", path))
    if not (full + "/").startswith(home.rstrip("/") + "/"):
        return False
    if env.project_dir and (full + "/").startswith(os.path.normpath(env.project_dir) + "/"):
        return False
    return not env.toplevel(full)


def decide(tool, inp, cwd, env=None):
    """Return (allowed, reason)."""
    env = env or Env(os.environ.get("CLAUDE_PROJECT_DIR", cwd or ""))
    inp = inp or {}
    if tool.startswith("mcp__"):
        return _mcp(tool, inp)
    if tool in ("WebFetch", "WebSearch"):
        return block("no web access from a Code Tower repo")
    if tool in ("Read", "Grep", "Glob", "LS", "NotebookRead"):
        keys = ("file_path", "path", "notebook_path") + (("pattern",) if tool == "Glob" else ()) + ("glob",)
        for key in keys:
            value = str(inp.get(key) or "")
            if value and (SECRET_PATH.search(value) or SECRET_IN_CMD.search(value)):
                return block("reading or searching secret or credential files is blocked")
            if value and key != "pattern" and _outside_repo_home(value, env):
                return block("reading outside the repo in the home folder is blocked")
        return True, "read"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if SECRET_PATH.search(path):
            return block("writing secret or credential files is blocked")
        if _protected_path(path, cwd, env):
            return block(f"{path} is part of the loop's own rules; the tower and workers never change it "
                         "(rules change only through a PR Adam merges)")
        return True, "file"
    if tool == "Bash":
        return _bash(str(inp.get("command", "")), cwd, env)
    return True, "not fenced"


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

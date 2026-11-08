"""PR checks. The pr-guard workflow runs main's copy of this file with main's rules/project.json, so a PR can't
change the rules that judge it. It reads the PR's files, diff, summary and labels as data and never runs the
PR's code.

Rules:
- Any PR that changes the loop's own rules (tamper paths) fails unless Adam has added the label
  `rules-approved` by hand. The tower can't add labels (its fence allows reads, draft PRs and comments only).
  New commits after the label fail again until Adam removes and re-adds it (his approval covers what he saw).
- Row PRs (tower/<id> or claude/tower-<id>) may change only worker paths and are capped in size. Their code under
  src/ is read (never run) and may use only a short list of standard modules: plain functions, with no classes,
  match statements, exits, printing, file access, dynamic lookups or private attributes, so it can't fake an exit
  check. No pytest config, compiled files or agent instructions (CLAUDE.md, .claude/) at any depth.
- No secret-like text in added lines or in the PR summary; no secret files.
- The summary has the four sections and the approval line after Undo.

Usage: python3 -I pr_check.py --branch REF --files FILES.txt --diff DIFF.txt --body BODY.txt --rules PROJECT.json
                              [--labels "a,b"] [--event ACTION] [--code DIR]
  --code DIR   the PR's changed src/ Python files, copied from the PR head as data (DIR/src/...)
Prints one problem per line. Exit 1 if any (the check fails) · 0 if none.
"""
import argparse
import ast
import json
import os
import re
import sys

REQUIRED = ("## What changed", "## Risks", "## How to test", "## Undo")
APPROVALS = ("Adam approves merge", "Adam approves production deploy")
RULES_LABEL = "rules-approved"
ROW_BRANCH = re.compile(r"^(tower/|claude/tower-)[a-z0-9]+$")
DEFAULT_TAMPER = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/", ".claude/agents/",
                  ".claude/skills/", ".claude/commands/", ".github/", ".gitignore", "rules/", "checks/", "tower/",
                  "tests/fence/", "PLAN.md", "CLAUDE.md", "pyproject.toml", "src/textkit/__init__.py")
SECRETS = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Airtable token", re.compile(r"\bpat[A-Za-z0-9]{14}\.[a-f0-9]{64}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Notion token", re.compile(r"\b(ntn_[A-Za-z0-9]{40,}|secret_[A-Za-z0-9]{43})\b")),
)
SECRET_FILE = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*|credentials[^/]*)$", re.I)
HEADER = re.compile(r"^(\+\+\+|---) (a/|b/|/dev/null)")
# Files that change how Python or pytest starts (config, plugins, compiled code) are never part of a row PR.
BAD_FILE = re.compile(r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml|sitecustomize\.py|"
                      r"usercustomize\.py|[^/]+\.pth|[^/]+\.pyc|[^/]+\.so|[^/]+\.pyd)$", re.I)
# Files Claude Code reads as instructions or settings, at any depth.
AGENT_FILE = re.compile(r"(^|/)(\.claude(/|$)|CLAUDE\.md$|CLAUDE\.local\.md$|AGENTS\.md$|\.mcp\.json$)", re.I)
# A row PR's summary is written by a worker: no images, embeds or HTML character references (they can carry data out
# to another server when the page is viewed), and links only to this repo, Notion and Claude Code.
BODY_ENTITY = re.compile(r"&#|&[A-Za-z][A-Za-z0-9]{1,31};")
BODY_IMAGE = re.compile(r"!\[|<\s*(img|image|picture|source|video|audio|iframe|embed|object|svg|link|meta)\b|"
                        r"\bsrc\s*=|^\s{0,3}\[[^\]\n]+\]:\s*\S", re.I | re.M)
BODY_SCHEME = re.compile(r"\b(?:https?|ftps?|wss?|data|javascript|vbscript|file|blob)\s*:", re.I)
BODY_LINKS_OK = ("https://app.notion.com/", "https://www.notion.so/", "https://notion.so/",
                 "https://claude.com/claude-code", "https://claude.ai/code/")
CODE_IMPORTS = {"__future__", "re", "string", "unicodedata", "textwrap", "html", "math", "itertools", "functools",
                "collections", "dataclasses", "enum", "textkit"}
BAD_NAMES = {"print", "open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals",
             "locals", "vars", "breakpoint", "input", "exit", "quit", "help", "memoryview", "SystemExit",
             "BaseException", "KeyboardInterrupt", "GeneratorExit"}
BAD_ATTRS = BAD_NAMES | {"sys", "os", "posix", "nt", "subprocess", "builtins", "importlib", "modules", "system",
                         "popen", "shutil", "socket", "ctypes", "pty", "io", "pathlib", "runpy", "inspect", "types",
                         "gc", "pickle", "marshal", "copyreg", "atexit", "signal", "threading", "multiprocessing",
                         "site", "urllib", "http", "tempfile", "Formatter", "get_field", "get_value", "vformat",
                         "traceback", "tb", "stdout", "stderr", "bltns", "pkgutil", "resolve_name", "environ",
                         "getenv", "putenv", "get_data", "import_module", "iter_modules", "walk_packages", "find_spec",
                         "find_loader", "module_from_spec", "spec_from_file_location", "load_module", "exec_module"}
PRIVATE_ATTR = re.compile(r"^(_|(f|tb|gi|cr|ag|co)_)")


SUSPICIOUS = re.compile(r"\b(os\._exit|sys\.exit|SystemExit|atexit|sys\.argv|sys\.modules|__builtins__|builtins|"
                        r"subprocess|socket|urllib|http\.client|importlib|ctypes|exec\(|eval\(|__import__)")


def suspicious_src_lines(diff):
    """Added lines under src/ that could fake a check (exit early, read argv, patch imports) or reach the network."""
    current, found = "", []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[4:].removeprefix("b/")
        elif line.startswith("+") and not HEADER.match(line) and current.startswith("src/") and SUSPICIOUS.search(line):
            found.append(f"{current}: {line[1:].strip()[:60]}")
    return found


def code_problems(path, text):
    """Why row code could fake an exit check or reach past the fence; [] if it can't. Reads the code, never runs it.
    Not a sandbox: it refuses the known routes (exits, printing, files, dynamic lookups, private attributes)."""
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError) as exc:
        return [f"{path}: doesn't parse ({exc})"]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(f"imports {a.name}" for a in node.names if a.name not in CODE_IMPORTS)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module not in CODE_IMPORTS:
                found.add(f"imports from {node.module}")
            found.update(f"imports {a.name}" for a in node.names
                         if a.name == "*" or a.name.startswith("_") or a.name in BAD_ATTRS)
        elif isinstance(node, ast.Name) and (node.id in BAD_NAMES or (node.id.startswith("__") and node.id != "__all__")):
            found.add(f"uses {node.id}")
        elif isinstance(node, ast.Attribute) and (PRIVATE_ATTR.match(node.attr) or node.attr in BAD_ATTRS):
            found.add(f"uses .{node.attr}")
        elif isinstance(node, ast.Match):
            found.add("uses a match statement (class patterns read attributes by name)")
        elif isinstance(node, ast.ClassDef):
            found.add(f"defines class {node.name} (row code is plain functions)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("__"):
            found.add(f"defines {node.name}")
    return [f"{path}: {p}" for p in sorted(found)]


def body_problems(body, repo):
    """Why a worker-written PR summary may not stand: images, embeds, HTML character references or outside links."""
    out = []
    if BODY_ENTITY.search(body):
        out.append("the PR summary has HTML character references (&#...; or &name;)")
    if BODY_IMAGE.search(body):
        out.append("the PR summary has an image or embed (![...], <img>, src=, or a [x]: link definition)")
    own = f"https://github.com/{repo.lower()}"
    for match in BODY_SCHEME.finditer(body.lower()):
        rest = body.lower()[match.start():]
        if repo and rest.startswith(own) and (len(rest) == len(own) or rest[len(own)] in "/)]}>\"' \t\n.,;#?"):
            continue
        if not any(rest.startswith(ok) for ok in BODY_LINKS_OK):
            out.append(f"the PR summary links outside this repo, Notion and Claude Code ({rest[:40]!r})")
            break
    return out


def is_tamper(path, tamper):
    for item in tamper:
        if item.endswith("/"):
            if path == item[:-1] or path.startswith(item):
                return True
        elif path == item:
            return True
    return False


def problems(branch, files, diff, body, rules, labels=(), event="", code=None):
    """Pure function: every problem with this PR, as plain one-line strings. `code` maps changed src/ paths to
    their text at the PR head."""
    out = []
    lines = diff.splitlines()
    added = [l[1:] for l in lines if l.startswith("+") and not HEADER.match(l)]
    removed = [l for l in lines if l.startswith("-") and not HEADER.match(l)]
    for name, pattern in SECRETS:
        if any(pattern.search(l) for l in added):
            out.append(f"secret-like text in added lines ({name})")
        if pattern.search(body):
            out.append(f"secret-like text in the PR summary ({name})")
    for f in files:
        if SECRET_FILE.search(f):
            out.append(f"secret or credential file in the PR: {f}")
    tamper = tuple(rules.get("tamper_paths", ())) + DEFAULT_TAMPER
    touched = [f for f in files if is_tamper(f, tamper)]
    if touched and RULES_LABEL not in labels:
        out.append("this PR changes the loop's own rules (" + ", ".join(touched) + "); it passes only after Adam "
                   f"adds the '{RULES_LABEL}' label")
    elif touched and event == "synchronize":
        out.append(f"new commits after '{RULES_LABEL}': Adam's approval covers what he saw, so he removes and "
                   "re-adds the label once he has looked at them")
    if ROW_BRANCH.match(branch):
        if touched:
            out.append("a row PR may never change the loop's own rules: " + ", ".join(touched))
        allowed = tuple(rules.get("worker_paths", ()))
        outside = [f for f in files if f not in touched
                   and not any(f == p.rstrip("/") or (p.endswith("/") and f.startswith(p)) for p in allowed)]
        if outside:
            out.append("a row PR changes files outside the worker paths: " + ", ".join(outside))
        cap = int(rules.get("caps", {}).get("pr_changed_lines", 200))
        changed = len(added) + len(removed)
        if changed > cap:
            out.append(f"{changed} changed lines; row PRs are capped at {cap}")
        flagged = suspicious_src_lines(diff)
        if flagged:
            out.append("a row PR adds code that exits early, reads argv, or reaches the network or imports "
                       "(Adam reads it before any merge): " + "; ".join(flagged[:3]))
        bad_files = [f for f in files if BAD_FILE.search(f)]
        if bad_files:
            out.append("a row PR may not add pytest config, plugins or compiled files: " + ", ".join(bad_files))
        out.extend(body_problems(body, str(rules.get("github_repo", ""))))
        agent_files = [f for f in files if AGENT_FILE.search(f)]
        if agent_files:
            out.append("a row PR may not add agent instructions or settings (CLAUDE.md, .claude/, .mcp.json): "
                       + ", ".join(agent_files))
        src_py = [f for f in files if f.startswith("src/") and f.endswith(".py") and f not in touched]
        code = code if code is not None else {}
        for f in src_py:
            if f not in code:
                if any(l.startswith(f"+++ b/{f}") for l in lines):      # added or changed, but not supplied
                    out.append(f"{f}: the PR guard couldn't read this file, so it can't check it")
                continue
            found = code_problems(f, code[f])
            if found:
                out.append("row code could fake a check or reach past the fence: " + "; ".join(found[:4]))
    for heading in REQUIRED:
        if heading not in body:
            out.append(f"the PR summary is missing '{heading}'")
    # The approval line closes the summary: on its own line after the Undo section. A tool footer may follow.
    after_undo = body.split("## Undo", 1)[1] if "## Undo" in body else ""
    if not any(l.strip() in APPROVALS for l in after_undo.splitlines()):
        out.append("the PR summary must close with 'Adam approves merge' on its own line after the Undo section")
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    for flag in ("--branch", "--files", "--diff", "--body", "--rules"):
        ap.add_argument(flag, required=True)
    ap.add_argument("--labels", default="")
    ap.add_argument("--event", default="")
    ap.add_argument("--code", default="")
    a = ap.parse_args(argv)
    try:
        files = [f.strip() for f in open(a.files, encoding="utf-8") if f.strip()]
        diff = open(a.diff, encoding="utf-8", errors="replace").read()
        body = open(a.body, encoding="utf-8", errors="replace").read()
        rules = json.load(open(a.rules, encoding="utf-8"))
        code = {}
        if a.code:
            for folder, _dirs, names in os.walk(a.code):
                for name in names:
                    full = os.path.join(folder, name)
                    code[os.path.relpath(full, a.code).replace(os.sep, "/")] = open(
                        full, encoding="utf-8", errors="replace").read()
    except (OSError, ValueError) as exc:
        print(f"pr-guard: can't read inputs ({exc}); failing closed")
        return 1
    labels = [l.strip() for l in a.labels.split(",") if l.strip()]
    found = problems(a.branch, files, diff, body, rules, labels, a.event, code if a.code else None)
    for p in found:
        print(p)
    if not found:
        print("pr-guard: no problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

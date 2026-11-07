"""PR checks. The pr-guard workflow runs main's copy of this file with main's rules/project.json, so a PR can't
change the rules that judge it. It reads the PR's files, diff and body as data and never runs the PR's code.

Usage: python3 -I pr_check.py --branch REF --files FILES.txt --diff DIFF.txt --body BODY.txt --rules PROJECT.json
Prints one problem per line. Exit 1 if any (the check fails) · 0 if none.
"""
import argparse
import json
import re
import sys

REQUIRED = ("## What changed", "## Risks", "## How to test", "## Undo")
APPROVALS = ("Adam approves merge", "Adam approves production deploy")
DEFAULT_TAMPER = (".claude/", ".github/", "rules/", "checks/", "tower/", "tests/fence/",
                  "PLAN.md", "CLAUDE.md", "pyproject.toml")
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


def is_tamper(path, tamper):
    for item in tamper:
        if item.endswith("/"):
            if path == item[:-1] or path.startswith(item):
                return True
        elif path == item:
            return True
    return False


def problems(branch, files, diff, body, rules):
    """Pure function: every problem with this PR, as plain one-line strings."""
    out = []
    lines = diff.splitlines()
    added = [l[1:] for l in lines if l.startswith("+") and not HEADER.match(l)]
    removed = [l for l in lines if l.startswith("-") and not HEADER.match(l)]
    for name, pattern in SECRETS:
        if any(pattern.search(l) for l in added):
            out.append(f"secret-like text in added lines ({name})")
    for f in files:
        if SECRET_FILE.search(f):
            out.append(f"secret or credential file in the PR: {f}")
    if branch.startswith("tower/"):
        tamper = tuple(rules.get("tamper_paths", ())) + DEFAULT_TAMPER
        touched = [f for f in files if is_tamper(f, tamper)]
        if touched:
            out.append("a tower PR changes the loop's own rules: " + ", ".join(touched))
        allowed = tuple(rules.get("worker_paths", ()))
        outside = [f for f in files if f not in touched
                   and not any(f == p.rstrip("/") or (p.endswith("/") and f.startswith(p)) for p in allowed)]
        if outside:
            out.append("a tower PR changes files outside the worker paths: " + ", ".join(outside))
        cap = int(rules.get("caps", {}).get("pr_changed_lines", 200))
        changed = len(added) + len(removed)
        if changed > cap:
            out.append(f"{changed} changed lines; tower PRs are capped at {cap}")
    for heading in REQUIRED:
        if heading not in body:
            out.append(f"the PR summary is missing '{heading}'")
    # The approval line closes the summary: it must sit on its own line after the Undo section. A tool footer
    # (e.g. "Generated with Claude Code") may follow it.
    after_undo = body.split("## Undo", 1)[1] if "## Undo" in body else ""
    if not any(l.strip() in APPROVALS for l in after_undo.splitlines()):
        out.append("the PR summary must close with 'Adam approves merge' on its own line after the Undo section")
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    for flag in ("--branch", "--files", "--diff", "--body", "--rules"):
        ap.add_argument(flag, required=True)
    a = ap.parse_args(argv)
    try:
        files = [f.strip() for f in open(a.files, encoding="utf-8") if f.strip()]
        diff = open(a.diff, encoding="utf-8", errors="replace").read()
        body = open(a.body, encoding="utf-8", errors="replace").read()
        rules = json.load(open(a.rules, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"pr-guard: can't read inputs ({exc}); failing closed")
        return 1
    found = problems(a.branch, files, diff, body, rules)
    for p in found:
        print(p)
    if not found:
        print("pr-guard: no problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

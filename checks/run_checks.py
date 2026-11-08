"""SENSE: run the exit checks in PLAN.md's ```json exit-checks block and print one JSON report.

Usage: python3 -I checks/run_checks.py [--plan PATH] [--only ID] [--strict]
Exit 0 = report printed (even when checks fail) · 1 = the plan block is missing or invalid · 2 = --strict and a
check failed.
A check passes only when its script exits 0 AND its last line of output is "<ID> pass" (so a module that exits
early can't fake a pass). Only commands shaped "python3 -I checks/<name>.py" run.
"""
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCK = re.compile(r"```json exit-checks[ \t]*\n(.*?)\n```", re.S)
SAFE_COMMAND = re.compile(r"^python3 -I checks/[a-z0-9_]+\.py$")


def load_block(text):
    """Parse and validate the exit-check block. Raises ValueError with a plain reason."""
    match = BLOCK.search(text)
    if not match:
        raise ValueError("PLAN.md has no ```json exit-checks block")
    data = json.loads(match.group(1))
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("the exit-check block has no checks")
    seen = set()
    for check in checks:
        for key in ("id", "what", "command"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                raise ValueError(f"a check is missing '{key}': {check}")
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", check["id"]):
            raise ValueError(f"check id {check['id']!r} must be capital letters and digits")
        if check["id"] in seen:
            raise ValueError(f"duplicate check id {check['id']}")
        seen.add(check["id"])
        check.setdefault("verifier", "machine")
        check.setdefault("depends_on", [])
    for check in checks:
        for dep in check["depends_on"]:
            if dep not in seen:
                raise ValueError(f"{check['id']} depends on unknown check {dep}")
    return data


def run_check(check, root=ROOT, timeout=60):
    """Run one check command from the repo root. Returns {id, pass, error, output}."""
    if not SAFE_COMMAND.match(check["command"]):
        return {"id": check["id"], "pass": False, "error": "command not allowed", "output": ""}
    try:
        proc = subprocess.run(shlex.split(check["command"]), cwd=root, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"id": check["id"], "pass": False, "error": "timeout", "output": ""}
    out_lines = proc.stdout.strip().splitlines()
    said_pass = bool(out_lines) and out_lines[-1].strip() == f"{check['id']} pass"
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    error = None if proc.returncode != 0 or said_pass else "exited 0 without printing its pass line"
    return {"id": check["id"], "pass": proc.returncode == 0 and said_pass, "error": error, "output": "\n".join(tail)}


def report(plan_text, root=ROOT, only=None):
    data = load_block(plan_text)
    checks = [c for c in data["checks"] if only is None or c["id"] == only]
    if only is not None and not checks:
        raise ValueError(f"no exit check with id {only}")
    results = [run_check(c, root) for c in checks]
    passing = sum(1 for r in results if r["pass"])
    return {"phase": data.get("phase", ""), "passing": passing, "total": len(results),
            "checks": results, "plan_checks": data["checks"]}


def main(argv):
    plan, only = ROOT / "PLAN.md", None
    if "--plan" in argv:
        plan = Path(argv[argv.index("--plan") + 1])
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    try:
        out = report(plan.read_text(encoding="utf-8"), root=plan.resolve().parent, only=only)
    except (OSError, ValueError, IndexError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2))
    if "--strict" in argv and out["passing"] < out["total"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

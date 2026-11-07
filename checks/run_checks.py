"""SENSE: run the exit checks in PLAN.md's ```json exit-checks block and print one JSON report.

Usage: python3 -I checks/run_checks.py [--plan PATH] [--strict]
Exit 0 = report printed (even when checks fail) · 1 = the plan block is missing or invalid · 2 = --strict and a check failed.
Only commands shaped "python3 -I checks/<name>.py" run, so editing the plan can't make the tower run anything else.
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
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    return {"id": check["id"], "pass": proc.returncode == 0, "error": None, "output": "\n".join(tail)}


def report(plan_text, root=ROOT):
    data = load_block(plan_text)
    results = [run_check(c, root) for c in data["checks"]]
    passing = sum(1 for r in results if r["pass"])
    return {"phase": data.get("phase", ""), "passing": passing, "total": len(results),
            "checks": results, "plan_checks": data["checks"]}


def main(argv):
    plan = ROOT / "PLAN.md"
    if "--plan" in argv:
        plan = Path(argv[argv.index("--plan") + 1])
    try:
        out = report(plan.read_text(encoding="utf-8"), root=plan.resolve().parent)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2))
    if "--strict" in argv and out["passing"] < out["total"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

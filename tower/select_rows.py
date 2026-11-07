"""SELECT: pick the rows the tower works this round. Code decides; the AI that will do the work never picks it.

Usage: python3 -I tower/select_rows.py CHECKS_JSON HEADS_TXT [--returns RETURNS_JSON] [--done-tonight N]
                                       [--rules rules/project.json]
  CHECKS_JSON   output of checks/run_checks.py
  HEADS_TXT     output of: git ls-remote --heads origin 'tower/*'
  RETURNS_JSON  optional list of {"id", "title", "pr", "branch", "done_test"}: tower PRs where a requester asked
                for changes (the tower reads the review; code ranks it)
Prints {"selected": [...], "skipped": [...], "counts": {...}}. Exit 0 ok · 1 bad input.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_RANK = {"return": 0, "exit_gap": 1, "follow_up": 2, "free_form": 3}
UNDO = "Close the draft PR and delete the branch."


def claimed_ids(heads_text):
    """Row IDs that already have a tower/<id> branch on the remote (their claim)."""
    ids = set()
    for line in heads_text.splitlines():
        match = re.search(r"refs/heads/tower/([A-Za-z0-9]+)(?:[-/]|\s*$)", line.strip())
        if match:
            ids.add(match.group(1).upper())
    return ids


def ai_ready(row, rules):
    gate = rules["ai_ready"]
    return (row["value"] >= gate["min_value"] and row["risk"] <= gate["max_risk"]
            and (bool(row["done_test"]) or not gate["needs_done_test"]))


def select(report, claimed, rules, returns=(), done_tonight=0):
    """Pure function: report from run_checks, claimed IDs, rules dict, return rows, rows already done tonight."""
    plan_checks = {c["id"]: c for c in report["plan_checks"]}
    passing = {r["id"] for r in report["checks"] if r["pass"]}
    failing = [r for r in report["checks"] if not r["pass"]]
    waiting_on = {}
    for check in report["plan_checks"]:
        if check["id"] in passing:
            continue
        for dep in check.get("depends_on", []):
            waiting_on[dep] = waiting_on.get(dep, 0) + 1
    values = rules["values"]
    rows, skipped = [], []

    for ret in returns:
        rows.append({"id": ret["id"], "title": ret["title"], "source": "return", "value": values["return"],
                     "risk": 0, "tier": "T1", "done_test": ret.get("done_test", ""), "verifier": "machine",
                     "depends_on": [], "branch": ret["branch"], "pr": ret.get("pr"), "undo": UNDO})
    return_ids = {r["id"].upper() for r in rows}

    for result in failing:
        check = plan_checks[result["id"]]
        if result.get("error") == "command not allowed":
            skipped.append({"id": check["id"], "why": "its check command isn't allowed; fix PLAN.md (rules PR)"})
            continue
        if check["id"].upper() in return_ids:
            continue
        if check["id"].upper() in claimed:
            skipped.append({"id": check["id"], "why": "claimed: a tower/ branch already exists"})
            continue
        open_deps = [d for d in check.get("depends_on", []) if d not in passing]
        if open_deps:
            skipped.append({"id": check["id"], "why": "waits on " + ", ".join(open_deps)})
            continue
        rows.append({"id": check["id"], "title": check["what"], "source": "exit_gap",
                     "value": values["exit_gap"] + (1 if waiting_on.get(check["id"]) else 0), "risk": 0,
                     "tier": "T1", "done_test": check["command"], "verifier": check.get("verifier", "machine"),
                     "depends_on": check.get("depends_on", []), "branch": f"tower/{check['id'].lower()}",
                     "pr": None, "undo": UNDO})

    ready = []
    for row in rows:
        if ai_ready(row, rules):
            ready.append(row)
        else:
            skipped.append({"id": row["id"], "why": "not AI-ready (value, risk or done test)"})
    ready.sort(key=lambda r: (r["verifier"] != "machine", SOURCE_RANK[r["source"]], -r["value"], r["id"]))

    caps = rules["caps"]
    room = max(0, min(caps["workers_per_round"], caps["draft_prs_per_night"] - done_tonight))
    selected, over = ready[:room], ready[room:]
    skipped += [{"id": r["id"], "why": "over this round's cap"} for r in over]
    return {"selected": selected, "skipped": skipped,
            "counts": {"failing": len(failing), "ready": len(ready), "selected": len(selected),
                       "skipped": len(skipped), "room": room,
                       "distance": f"{report['passing']}/{report['total']} exit checks pass"}}


def main(argv):
    try:
        args = list(argv)
        opts = {}
        for flag in ("--returns", "--done-tonight", "--rules"):
            if flag in args:
                i = args.index(flag)
                opts[flag] = args[i + 1]
                del args[i:i + 2]
        checks_path, heads_path = args
        report = json.loads(Path(checks_path).read_text(encoding="utf-8"))
        heads = Path(heads_path).read_text(encoding="utf-8")
        rules = json.loads(Path(opts.get("--rules", ROOT / "rules" / "project.json")).read_text(encoding="utf-8"))
        returns = json.loads(Path(opts["--returns"]).read_text(encoding="utf-8")) if "--returns" in opts else []
        done = int(opts.get("--done-tonight", 0))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": f"bad input: {exc}"}))
        return 1
    print(json.dumps(select(report, claimed_ids(heads), rules, returns, done), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

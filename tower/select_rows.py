"""SELECT: pick the rows the tower works this round. Code decides; the AI that will do the work never picks it.

Usage: python3 -I tower/select_rows.py CHECKS_JSON HEADS_TXT [--prs PRS_JSON] [--now ISO8601]
                                       [--returns RETURNS_JSON] [--rules rules/project.json]
  CHECKS_JSON   output of checks/run_checks.py
  HEADS_TXT     output of: git ls-remote --heads origin
  PRS_JSON      output of: gh api repos/<repo>/pulls -X GET -f state=all -f per_page=100  (counts tonight's PRs)
  RETURNS_JSON  [{"id", "title", "pr", "branch"}]: open row PRs where Adam asked for changes. Code checks each one
                (the branch must be that row's branch and the PR must be open) and takes the done test from PLAN.md.
Prints {"selected": [...], "skipped": [...], "counts": {...}}. Exit 0 ok · 1 bad input.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_RANK = {"return": 0, "exit_gap": 1, "follow_up": 2, "free_form": 3}
ROW_BRANCH = re.compile(r"^(tower/|claude/tower-)([a-z0-9]+)$")
UNDO = "Close the draft PR and delete the branch."


def claimed_ids(heads_text):
    """Row IDs that already have a row branch on the remote (their claim)."""
    ids = set()
    for line in heads_text.splitlines():
        ref = line.strip().split("\t")[-1].replace("refs/heads/", "")
        match = ROW_BRANCH.match(ref)
        if match:
            ids.add(match.group(2).upper())
    return ids


def prs_tonight(prs, now):
    """Row PRs opened in the 24 hours before `now` (the nightly cap counts these, not a number the AI supplies)."""
    count = 0
    for pr in prs:
        head = ((pr.get("head") or {}).get("ref")) or ""
        created = pr.get("created_at") or ""
        try:
            when = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ROW_BRANCH.match(head) and timedelta(0) <= now - when <= timedelta(hours=24):
            count += 1
    return count


def open_row_prs(prs):
    return {((pr.get("head") or {}).get("ref")): pr.get("number") for pr in prs
            if pr.get("state") == "open" and ROW_BRANCH.match(((pr.get("head") or {}).get("ref")) or "")}


def ai_ready(row, rules):
    gate = rules["ai_ready"]
    return (row["value"] >= gate["min_value"] and row["risk"] <= gate["max_risk"]
            and (bool(row["done_test"]) or not gate["needs_done_test"]))


def branch_for(row_id, rules):
    return f"{rules.get('row_branch_prefix', 'tower/')}{row_id.lower()}"


def select(report, claimed, rules, returns=(), prs=(), now=None):
    """Pure function: run_checks report, claimed IDs, rules, return rows, the PR list, and the time now."""
    now = now or datetime.now(timezone.utc)
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
    open_prs = open_row_prs(prs)
    rows, skipped = [], []

    for ret in returns:
        rid = str(ret.get("id", "")).upper()
        check = plan_checks.get(rid)
        branch = str(ret.get("branch", ""))
        match = ROW_BRANCH.match(branch)
        if not check or not match or match.group(2).upper() != rid or open_prs.get(branch) != ret.get("pr"):
            skipped.append({"id": rid or "?", "why": "return row rejected: not an open row PR on that row's branch"})
            continue
        rows.append({"id": rid, "title": str(ret.get("title") or check["what"]), "source": "return",
                     "value": values["return"], "risk": 0, "tier": "T1", "done_test": check["command"],
                     "verifier": check.get("verifier", "machine"), "depends_on": [], "branch": branch,
                     "pr": ret.get("pr"), "mode": "fix", "undo": UNDO})
    return_ids = {r["id"] for r in rows}

    for result in failing:
        check = plan_checks[result["id"]]
        if result.get("error") == "command not allowed":
            skipped.append({"id": check["id"], "why": "its check command isn't allowed; fix PLAN.md (rules PR)"})
            continue
        if check["id"] in return_ids:
            continue
        if check["id"].upper() in claimed:
            skipped.append({"id": check["id"], "why": "claimed: its row branch already exists"})
            continue
        open_deps = [d for d in check.get("depends_on", []) if d not in passing]
        if open_deps:
            skipped.append({"id": check["id"], "why": "waits on " + ", ".join(open_deps)})
            continue
        rows.append({"id": check["id"], "title": check["what"], "source": "exit_gap",
                     "value": values["exit_gap"] + (1 if waiting_on.get(check["id"]) else 0), "risk": 0,
                     "tier": "T1", "done_test": check["command"], "verifier": check.get("verifier", "machine"),
                     "depends_on": check.get("depends_on", []), "branch": branch_for(check["id"], rules),
                     "pr": None, "mode": "fresh", "undo": UNDO})

    ready = []
    for row in rows:
        if ai_ready(row, rules):
            ready.append(row)
        else:
            skipped.append({"id": row["id"], "why": "not AI-ready (value, risk or done test)"})
    ready.sort(key=lambda r: (r["verifier"] != "machine", SOURCE_RANK[r["source"]], -r["value"], r["id"]))

    caps = rules["caps"]
    done = prs_tonight(prs, now)
    room = max(0, min(caps["workers_per_round"], caps["draft_prs_per_night"] - done))
    fix_rows = [r for r in ready if r["mode"] == "fix"]            # fixes don't open new PRs
    new_rows = [r for r in ready if r["mode"] == "fresh"]
    selected = fix_rows[:caps["workers_per_round"]]
    selected += new_rows[:max(0, min(room, caps["workers_per_round"] - len(selected)))]
    skipped += [{"id": r["id"], "why": "over this round's cap"} for r in ready if r not in selected]
    return {"selected": selected, "skipped": skipped,
            "counts": {"failing": len(failing), "ready": len(ready), "selected": len(selected),
                       "skipped": len(skipped), "prs_last_24h": done, "room": room,
                       "distance": f"{report['passing']}/{report['total']} exit checks pass"}}


def main(argv):
    try:
        args, opts = list(argv), {}
        for flag in ("--returns", "--prs", "--now", "--rules"):
            if flag in args:
                i = args.index(flag)
                opts[flag] = args[i + 1]
                del args[i:i + 2]
        checks_path, heads_path = args
        report = json.loads(Path(checks_path).read_text(encoding="utf-8"))
        heads = Path(heads_path).read_text(encoding="utf-8")
        rules = json.loads(Path(opts.get("--rules", ROOT / "rules" / "project.json")).read_text(encoding="utf-8"))
        returns = json.loads(Path(opts["--returns"]).read_text(encoding="utf-8")) if "--returns" in opts else []
        prs = json.loads(Path(opts["--prs"]).read_text(encoding="utf-8")) if "--prs" in opts else []
        now = datetime.fromisoformat(opts["--now"].replace("Z", "+00:00")) if "--now" in opts else None
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(json.dumps({"error": f"bad input: {exc}"}))
        return 1
    print(json.dumps(select(report, claimed_ids(heads), rules, returns, prs, now), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

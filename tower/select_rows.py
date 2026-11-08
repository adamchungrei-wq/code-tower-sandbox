"""SELECT: pick the rows the tower works this round. Code decides; the AI that will do the work never picks it.

Usage: python3 -I tower/select_rows.py [--returns runs/ct/returns.json]
Code gathers its own evidence, so nothing the AI writes can change the count or the cap:
  - the exit-check report: python3 -I checks/run_checks.py
  - the remote's row branches: git ls-remote --heads origin
  - every PR, open or closed: gh api repos/<repo>/pulls -X GET -f state=all -f per_page=100 (tonight's cap)
  - the rules: rules/project.json in this checkout (the tower runs detached at origin/main)
RETURNS: [{"id", "title", "pr", "branch"}]: open row PRs where Adam asked for changes. Code checks each one on
GitHub (the PR is open on that row's branch, and a login in `requesters` commented without the tower's marker
after the PR's newest commit) and takes the done test from PLAN.md.
Row branches with no PR (an earlier run stopped after its push) come back as mode "recover" rows; like new rows
they open a PR, so they count against the nightly cap. PRs the guard has allowed but GitHub doesn't list yet (its
ledger in the git folder) count too, so SELECT and the guard agree on the room left. The clock is the machine's.
Prints {"selected": [...], "skipped": [...], "counts": {...}}. Exit 0 ok · 1 bad input · 2 evidence unavailable.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_RANK = {"return": 0, "exit_gap": 1, "follow_up": 2, "free_form": 3}
ROW_BRANCH = re.compile(r"^(tower/|claude/tower-)([a-z0-9]+)$")
UNDO = "Close the draft PR and delete the branch."
MARKER = "<!-- code-tower -->"


def claimed_branches(heads_text):
    """{row ID: [row branches on the remote]} — a row branch is its row's claim."""
    out = {}
    for line in heads_text.splitlines():
        ref = line.strip().split("\t")[-1].replace("refs/heads/", "")
        match = ROW_BRANCH.match(ref)
        if match:
            out.setdefault(match.group(2).upper(), []).append(ref)
    return out


def claimed_ids(heads_text):
    """Row IDs that already have a row branch on the remote (their claim)."""
    return set(claimed_branches(heads_text))


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


def pending_prs(ledger, prs, now):
    """PR calls the guard allowed in the last 24 hours that GitHub doesn't list (yet): its ledger minus GitHub."""
    seen = []
    for pr in prs:
        head = ((pr.get("head") or {}).get("ref")) or ""
        when = _when(pr.get("created_at"))
        if ROW_BRANCH.match(head) and when:
            seen.append((head, when))
    count = 0
    for entry in ledger if isinstance(ledger, list) else []:
        at = _when(entry.get("at")) if isinstance(entry, dict) else None
        if not at or not timedelta(0) <= now - at <= timedelta(hours=24):
            continue
        if not any(head == entry.get("head") and when >= at - timedelta(minutes=5) for head, when in seen):
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


def select(report, claimed, rules, returns=(), prs=(), now=None, claimed_branches=None, pending=0):
    """Pure function: run_checks report, claimed IDs, rules, return rows, the PR list, the time now, and the
    claimed branch names (for recovery)."""
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
    any_pr = {((pr.get("head") or {}).get("ref")) for pr in prs}          # any state: open, closed or merged
    rows, skipped = [], []

    for ret in returns:
        rid = str(ret.get("id", "")).upper()
        check = plan_checks.get(rid)
        branch = str(ret.get("branch", ""))
        match = ROW_BRANCH.match(branch)
        if (not check or not match or match.group(2).upper() != rid or ret.get("pr") is None
                or open_prs.get(branch) != ret.get("pr")):
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
            branches = (claimed_branches or {}).get(check["id"].upper(), [])
            if any(b in open_prs for b in branches):
                skipped.append({"id": check["id"], "why": "claimed: its row branch has an open PR"})
            elif any(b in any_pr for b in branches):
                skipped.append({"id": check["id"], "why": "its PR was closed (Adam's call); delete the branch to "
                                                          "let the row run again"})
            elif branches:
                rows.append({"id": check["id"], "title": check["what"], "source": "exit_gap",
                             "value": values["exit_gap"], "risk": 0, "tier": "T1", "done_test": check["command"],
                             "verifier": check.get("verifier", "machine"), "depends_on": [], "branch": branches[0],
                             "pr": None, "mode": "recover", "undo": UNDO})
            else:
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
    mode_rank = {"fix": 0, "recover": 1, "fresh": 2}
    ready.sort(key=lambda r: (mode_rank[r["mode"]], r["verifier"] != "machine", SOURCE_RANK[r["source"]],
                              -r["value"], r["id"]))

    caps = rules["caps"]
    done = prs_tonight(prs, now) + pending
    room = max(0, min(caps["workers_per_round"], caps["draft_prs_per_night"] - done))
    fix_rows = [r for r in ready if r["mode"] == "fix"]            # fixes don't open new PRs
    new_rows = [r for r in ready if r["mode"] in ("recover", "fresh")]   # these do: they share the nightly room
    selected = fix_rows[:caps["workers_per_round"]]
    selected += new_rows[:max(0, min(room, caps["workers_per_round"] - len(selected)))]
    skipped += [{"id": r["id"], "why": "over this round's cap"} for r in ready if r not in selected]
    return {"selected": selected, "skipped": skipped,
            "counts": {"failing": len(failing), "ready": len(ready), "selected": len(selected),
                       "skipped": len(skipped), "prs_last_24h": done, "room": room,
                       "distance": f"{report['passing']}/{report['total']} exit checks pass"}}


def gather(root=ROOT, run=subprocess.run):
    """Collect the evidence with code: rules, the exit-check report, the remote's heads and GitHub's PR list."""
    rules = json.loads((Path(root) / "rules" / "project.json").read_text(encoding="utf-8"))

    def output(args):
        proc = run(args, cwd=str(root), capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"`{' '.join(args[:3])}` failed: {(proc.stderr or proc.stdout).strip()[-200:]}")
        return proc.stdout

    report = json.loads(output([sys.executable, "-I", "checks/run_checks.py"]))
    heads = output(["git", "ls-remote", "--heads", "origin"])
    prs = json.loads(output(["gh", "api", f"repos/{rules['github_repo']}/pulls", "-X", "GET", "-f", "state=all",
                             "-f", "per_page=100"]))
    if not isinstance(prs, list) or not isinstance(report, dict):
        raise RuntimeError("unexpected evidence shape")
    ledger = []
    try:
        common = output(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
        if Path(common).is_absolute():                # never a path relative to wherever this runs
            ledger = json.loads((Path(common) / "code-tower-prs.json").read_text(encoding="utf-8"))
    except (RuntimeError, OSError, ValueError):
        ledger = []                                   # no ledger yet: the guard still enforces the cap
    return rules, report, heads, prs, ledger


def _when(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def verified_returns(returns, rules, root=ROOT, run=subprocess.run):
    """Split return rows into (kept, skipped) using GitHub itself: the PR must be open on the row's branch, and a
    requester must have asked for changes (a comment without the tower's marker) after the PR's newest commit."""
    repo = rules["github_repo"]
    requesters = {str(r).lower() for r in rules.get("requesters", [])}

    def api(*args):
        proc = run(["gh", "api", *args], cwd=str(root), capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"gh api {args[0]} failed")
        return json.loads(proc.stdout)

    kept, skipped = [], []
    for ret in returns:
        rid = str(ret.get("id", "?")).upper() if isinstance(ret, dict) else "?"
        try:
            number = int(ret["pr"])
            pr = api(f"repos/{repo}/pulls/{number}")
            since = api(f"repos/{repo}/commits/{pr['head']['sha']}")["commit"]["committer"]["date"]
            comments = api(f"repos/{repo}/issues/{number}/comments", "-X", "GET", "-f", f"since={since}",
                           "-f", "per_page=100")
        except (KeyError, TypeError, ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            skipped.append({"id": rid, "why": "return row rejected: couldn't confirm it on GitHub"})
            continue
        last_commit = _when(since)
        asked = [c for c in comments if isinstance(c, dict)
                 and str((c.get("user") or {}).get("login", "")).lower() in requesters
                 and MARKER not in str(c.get("body", ""))
                 and _when(c.get("created_at")) and last_commit and _when(c.get("created_at")) > last_commit]
        if pr.get("state") == "open" and (pr.get("head") or {}).get("ref") == ret.get("branch") and asked:
            kept.append(ret)
        else:
            skipped.append({"id": rid, "why": "return row rejected: no change request from a requester on that open "
                                              "row PR since its newest commit"})
    return kept, skipped


def main(argv, root=ROOT, run=subprocess.run):
    try:
        args = list(argv)
        returns = []
        if args[:1] == ["--returns"] and len(args) == 2:
            returns = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        elif args:
            raise ValueError("usage: select_rows.py [--returns FILE]")
        if not isinstance(returns, list):
            raise ValueError("returns must be a JSON list")
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": f"bad input: {exc}"}))
        return 1
    try:
        rules, report, heads, prs, ledger = gather(root, run)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": f"evidence unavailable: {exc}"}))
        return 2
    kept, rejected = verified_returns(returns, rules, root, run) if returns else ([], [])
    pending = pending_prs(ledger, prs, datetime.now(timezone.utc))
    out = select(report, claimed_ids(heads), rules, kept, prs, None, claimed_branches(heads), pending)
    out["skipped"] = rejected + out["skipped"]
    out["counts"]["skipped"] = len(out["skipped"])
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

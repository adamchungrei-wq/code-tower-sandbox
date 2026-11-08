import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_checks
import select_rows

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / "rules" / "project.json").read_text(encoding="utf-8"))
PLAN_CHECKS = run_checks.load_block((ROOT / "PLAN.md").read_text(encoding="utf-8"))["checks"]
NOW = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)


def report(passing=()):
    checks = [{"id": c["id"], "pass": c["id"] in passing, "error": None, "output": ""} for c in PLAN_CHECKS]
    return {"phase": "P1", "passing": len(passing), "total": len(checks), "checks": checks, "plan_checks": PLAN_CHECKS}


def pr(number, ref, hours_ago=1, state="open"):
    return {"number": number, "state": state, "head": {"ref": ref},
            "created_at": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")}


def ids(rows):
    return [r["id"] for r in rows]


def test_fresh_plan_selects_the_three_independent_rows():
    out = select_rows.select(report(), set(), RULES, now=NOW)
    assert ids(out["selected"]) == ["E1", "E2", "E3"]
    assert {"id": "E4", "why": "waits on E1, E2, E3"} in out["skipped"]
    assert all(r["branch"] == f"tower/{r['id'].lower()}" and r["mode"] == "fresh" for r in out["selected"])


def test_rows_others_wait_on_score_higher():
    out = select_rows.select(report(), set(), RULES, now=NOW)
    assert all(r["value"] == RULES["values"]["exit_gap"] + 1 for r in out["selected"])


def test_claimed_rows_are_skipped():
    out = select_rows.select(report(), {"E1"}, RULES, now=NOW)
    assert ids(out["selected"]) == ["E2", "E3"]
    assert any(s["id"] == "E1" and "claimed" in s["why"] for s in out["skipped"])


def test_nightly_cap_is_counted_from_the_pr_list():
    prs = [pr(1, "tower/x1"), pr(2, "tower/x2"), pr(3, "rules/caps"), pr(4, "tower/x4", hours_ago=30)]
    out = select_rows.select(report(), set(), RULES, prs=prs, now=NOW)
    assert out["counts"]["prs_last_24h"] == 2 and ids(out["selected"]) == ["E1"]


def test_dependent_row_opens_when_its_dependencies_pass():
    out = select_rows.select(report(passing=("E1", "E2", "E3")), set(), RULES, now=NOW)
    assert ids(out["selected"]) == ["E4"]
    assert out["counts"]["distance"] == "3/4 exit checks pass"


def test_valid_return_row_goes_first_as_a_fix():
    prs = [pr(5, "tower/e2")]
    ret = [{"id": "E2", "title": "E2: address review", "pr": 5, "branch": "tower/e2"}]
    out = select_rows.select(report(), {"E2"}, RULES, returns=ret, prs=prs, now=NOW)
    first = out["selected"][0]
    assert first["id"] == "E2" and first["source"] == "return" and first["mode"] == "fix"
    assert first["done_test"] == "python3 -I checks/e2_word_count.py"      # from PLAN.md, not from the AI
    assert ids(out["selected"]).count("E2") == 1


def test_bad_return_rows_are_rejected():
    prs = [pr(5, "tower/e2")]
    for ret in ({"id": "E2", "pr": 5, "branch": "rules/caps"}, {"id": "E2", "pr": 9, "branch": "tower/e2"},
                {"id": "E1", "pr": 5, "branch": "tower/e2"}, {"id": "ZZ", "pr": 5, "branch": "tower/zz"}):
        out = select_rows.select(report(), {"E2"}, RULES, returns=[ret], prs=prs, now=NOW)
        assert all(r["source"] != "return" for r in out["selected"]), ret
        assert any("return row rejected" in s["why"] for s in out["skipped"]), ret


def test_claimed_ids_from_ls_remote():
    heads = ("a1\trefs/heads/tower/e1\nb2\trefs/heads/claude/tower-e10\nc3\trefs/heads/feature/x\n"
             "d4\trefs/heads/tower/e10-retry\n")
    assert select_rows.claimed_ids(heads) == {"E1", "E10"}


def test_a_branch_left_without_a_pr_comes_back_as_a_recover_row():
    branches = {"E1": ["tower/e1"]}
    out = select_rows.select(report(), {"E1"}, RULES, prs=[], now=NOW, claimed_branches=branches)
    first = out["selected"][0]
    assert first["id"] == "E1" and first["mode"] == "recover" and first["branch"] == "tower/e1"
    assert len(out["selected"]) == 3                          # recover shares the room with fresh rows


def test_open_or_closed_prs_stop_recovery():
    branches = {"E1": ["tower/e1"]}
    open_pr = select_rows.select(report(), {"E1"}, RULES, prs=[pr(7, "tower/e1")], now=NOW, claimed_branches=branches)
    assert "E1" not in ids(open_pr["selected"]) and any("open PR" in s["why"] for s in open_pr["skipped"])
    closed = select_rows.select(report(), {"E1"}, RULES, prs=[pr(7, "tower/e1", state="closed")], now=NOW,
                                claimed_branches=branches)
    assert "E1" not in ids(closed["selected"]) and any("closed" in s["why"] for s in closed["skipped"])


def test_return_rows_without_a_pr_number_are_rejected():
    ret = [{"id": "E2", "title": "x", "pr": None, "branch": "tower/e2"}]
    out = select_rows.select(report(), {"E2"}, RULES, returns=ret, prs=[], now=NOW)
    assert all(r["source"] != "return" for r in out["selected"])


# --- code gathers its own evidence (re-check #3 should-fix 8: an AI-written PR list can't reset the cap) ----------
class FakeRun:
    """Stands in for subprocess.run: answers run_checks, git ls-remote and gh api from fixed data."""

    def __init__(self, heads="", prs=(), fail=None, pr_detail=None, commit_date="2026-11-07T10:00:00Z", comments=(),
                 common=""):
        self.heads, self.prs, self.fail, self.calls, self.common = heads, list(prs), fail, [], common
        self.pr_detail, self.commit_date, self.comments = pr_detail or {}, commit_date, list(comments)

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        out, rc = "", 0
        route = args[2] if args[:2] == ["gh", "api"] and len(args) > 2 else ""
        if self.fail and self.fail in " ".join(args):
            rc = 1
        elif "checks/run_checks.py" in args:
            out = json.dumps(report())
        elif args[:2] == ["git", "ls-remote"]:
            out = self.heads
        elif args[:2] == ["git", "rev-parse"]:
            out = self.common
        elif route.endswith("/pulls"):
            out = json.dumps(self.prs)
        elif "/pulls/" in route:
            out = json.dumps(self.pr_detail)
        elif "/commits/" in route:
            out = json.dumps({"commit": {"committer": {"date": self.commit_date}}})
        elif route.endswith("/comments"):
            out = json.dumps(self.comments)
        return subprocess.CompletedProcess(args, rc, out, "boom" if rc else "")


def test_cli_gathers_checks_heads_and_prs_itself(capsys):
    just_now = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    prs = [{"number": n, "state": "open", "head": {"ref": f"tower/x{n}"}, "created_at": just_now}
           for n in range(3)]                                     # three row PRs tonight (machine clock): cap reached
    run = FakeRun(prs=prs)
    assert select_rows.main([], root=ROOT, run=run) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["selected"] == [] and out["counts"]["prs_last_24h"] == 3
    assert any(c[:2] == ["gh", "api"] and "state=all" in c for c in run.calls)
    assert any(c[:3] == ["git", "ls-remote", "--heads"] for c in run.calls)


def test_prs_the_guard_allowed_count_until_github_lists_them():
    # re-check #5 #2: GitHub's PR list lags a PR call by a while; the guard's ledger fills the gap
    def at(minutes):
        return (NOW - timedelta(minutes=minutes)).isoformat()
    ledger = [{"head": "tower/e1", "row": "E1", "at": at(3)},           # not on GitHub yet: counts
              {"head": "tower/e2", "row": "E2", "at": at(60)},          # GitHub lists it: counted once, from GitHub
              {"head": "tower/e3", "row": "E3", "at": at(60 * 30)},     # older than a day
              "junk", {"head": "tower/e4"}, {"head": "tower/e4", "at": "soon"}]
    prs = [pr(2, "tower/e2", hours_ago=0.9)]
    assert select_rows.pending_prs(ledger, prs, NOW) == 1
    assert select_rows.pending_prs({"not": "a list"}, prs, NOW) == 0
    out = select_rows.select(report(), set(), RULES, prs=prs, now=NOW, pending=2)
    assert out["counts"]["prs_last_24h"] == 3 and out["selected"] == []


def test_cli_counts_the_guards_ledger(tmp_path, capsys):
    just_now = datetime.now(timezone.utc) - timedelta(minutes=2)
    (tmp_path / "code-tower-prs.json").write_text(json.dumps(
        [{"head": f"tower/e{n}", "row": f"E{n}", "at": just_now.isoformat()} for n in (1, 2)]))
    assert select_rows.main([], root=ROOT, run=FakeRun(common=str(tmp_path))) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["prs_last_24h"] == 2 and ids(out["selected"]) == ["E1"]
    assert select_rows.main([], root=ROOT, run=FakeRun(common="relative/dir")) == 0      # never a relative path
    assert json.loads(capsys.readouterr().out)["counts"]["prs_last_24h"] == 0


def test_cli_takes_no_ai_supplied_evidence(tmp_path, capsys):
    fake = tmp_path / "prs.json"
    fake.write_text("[]")
    for argv in (["--prs", str(fake)], ["runs/ct/checks.json", "runs/ct/heads.txt"], ["--rules", str(fake)]):
        assert select_rows.main(argv, root=ROOT, run=FakeRun()) == 1, argv
        capsys.readouterr()


def test_cli_stops_when_evidence_is_unavailable(capsys):
    for broken in ("gh api", "ls-remote", "run_checks"):
        assert select_rows.main([], root=ROOT, run=FakeRun(fail=broken)) == 2, broken
        assert "evidence unavailable" in capsys.readouterr().out


def test_cli_return_rows_are_confirmed_on_github(tmp_path, capsys):
    # re-check #4 nit 11d: a return row now needs Adam's comment on GitHub, after the PR's newest commit
    returns = tmp_path / "returns.json"
    returns.write_text(json.dumps([{"id": "E1", "title": "x", "pr": 7, "branch": "tower/e1"}]))
    detail = {"state": "open", "head": {"ref": "tower/e1", "sha": "abc"}}
    adam = {"user": {"login": "adamchungrei-wq"}, "body": "Please also handle tabs.", "created_at": "2026-11-07T11:00:00Z"}
    base = dict(heads="abc\trefs/heads/tower/e1\n", prs=[pr(7, "tower/e1", hours_ago=30)], pr_detail=detail)
    assert select_rows.main(["--returns", str(returns)], root=ROOT, run=FakeRun(**base, comments=[adam])) == 0
    assert [(r["id"], r["mode"]) for r in json.loads(capsys.readouterr().out)["selected"]][:1] == [("E1", "fix")]
    for comments, detail_ in (([], detail),                                                    # nobody asked
                              ([{**adam, "body": "<!-- code-tower -->\nChecked."}], detail),  # the tower's own note
                              ([{**adam, "created_at": "2026-11-07T09:00:00Z"}], detail),     # before the newest commit
                              ([{**adam, "user": {"login": "someone-else"}}], detail),          # not a requester
                              ([adam], {**detail, "state": "closed"}),                          # PR closed
                              ([adam], {**detail, "head": {"ref": "tower/e2", "sha": "abc"}})):  # another branch
        run = FakeRun(**{**base, "pr_detail": detail_}, comments=comments)
        assert select_rows.main(["--returns", str(returns)], root=ROOT, run=run) == 0
        out = json.loads(capsys.readouterr().out)
        assert all(r["mode"] != "fix" for r in out["selected"]), (comments, detail_)
        assert any("return row rejected" in s["why"] for s in out["skipped"])

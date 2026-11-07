import json
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

import json
from pathlib import Path

import run_checks
import select_rows

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / "rules" / "project.json").read_text(encoding="utf-8"))
PLAN_CHECKS = run_checks.load_block((ROOT / "PLAN.md").read_text(encoding="utf-8"))["checks"]


def report(passing=()):
    checks = [{"id": c["id"], "pass": c["id"] in passing, "error": None, "output": ""} for c in PLAN_CHECKS]
    return {"phase": "P1", "passing": len(passing), "total": len(checks), "checks": checks, "plan_checks": PLAN_CHECKS}


def ids(rows):
    return [r["id"] for r in rows]


def test_fresh_plan_selects_the_three_independent_rows():
    out = select_rows.select(report(), set(), RULES)
    assert ids(out["selected"]) == ["E1", "E2", "E3"]
    assert {"id": "E4", "why": "waits on E1, E2, E3"} in out["skipped"]
    assert all(r["branch"] == f"tower/{r['id'].lower()}" and r["tier"] == "T1" for r in out["selected"])


def test_rows_others_wait_on_score_higher():
    out = select_rows.select(report(), set(), RULES)
    assert all(r["value"] == RULES["values"]["exit_gap"] + 1 for r in out["selected"])


def test_claimed_rows_are_skipped():
    out = select_rows.select(report(), {"E1"}, RULES)
    assert ids(out["selected"]) == ["E2", "E3"]
    assert any(s["id"] == "E1" and "claimed" in s["why"] for s in out["skipped"])


def test_nightly_pr_cap():
    out = select_rows.select(report(), set(), RULES, done_tonight=2)
    assert ids(out["selected"]) == ["E1"]
    assert out["counts"]["room"] == 1


def test_dependent_row_opens_when_its_dependencies_pass():
    out = select_rows.select(report(passing=("E1", "E2", "E3")), set(), RULES)
    assert ids(out["selected"]) == ["E4"]
    assert out["counts"]["distance"] == "3/4 exit checks pass"


def test_return_rows_go_first_and_are_not_duplicated():
    ret = [{"id": "E2", "title": "E2: address review", "pr": 5, "branch": "tower/e2",
            "done_test": "python3 -I checks/e2_word_count.py"}]
    out = select_rows.select(report(), {"E2"}, RULES, returns=ret)
    assert out["selected"][0]["id"] == "E2" and out["selected"][0]["source"] == "return"
    assert ids(out["selected"]).count("E2") == 1


def test_claimed_ids_from_ls_remote():
    heads = "a1\trefs/heads/tower/e1\nb2\trefs/heads/tower/e10-retry\nc3\trefs/heads/feature/x\n"
    assert select_rows.claimed_ids(heads) == {"E1", "E10"}

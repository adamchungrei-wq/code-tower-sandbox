from pathlib import Path

import pytest

import run_checks

ROOT = Path(__file__).resolve().parents[2]
PLAN = (ROOT / "PLAN.md").read_text(encoding="utf-8")


def test_plan_block_is_valid_and_every_command_is_runnable():
    data = run_checks.load_block(PLAN)
    assert [c["id"] for c in data["checks"]] == ["E1", "E2", "E3", "E4"]
    assert all(run_checks.SAFE_COMMAND.match(c["command"]) for c in data["checks"])
    for check in data["checks"]:
        assert (ROOT / check["command"].split()[-1]).is_file(), check["command"]


def block(checks_json):
    return "x\n```json exit-checks\n" + checks_json + "\n```\n"


def test_rejects_bad_blocks():
    with pytest.raises(ValueError):
        run_checks.load_block("no block here")
    with pytest.raises(ValueError):
        run_checks.load_block(block('{"checks": [{"id": "A", "what": "a", "command": "c", "depends_on": ["Z"]}]}'))
    with pytest.raises(ValueError):
        run_checks.load_block(block('{"checks": [{"id": "A", "what": "a", "command": "c"},'
                                    ' {"id": "A", "what": "b", "command": "d"}]}'))


def test_only_safe_commands_run(tmp_path):
    result = run_checks.run_check({"id": "X", "what": "x", "command": "rm -rf /"}, root=tmp_path)
    assert result["pass"] is False and result["error"] == "command not allowed"


def test_pass_and_fail(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "ok_check.py").write_text("print('ok')\n")
    (tmp_path / "checks" / "bad_check.py").write_text("raise SystemExit(1)\n")
    ok = run_checks.run_check({"id": "A", "what": "a", "command": "python3 -I checks/ok_check.py"}, root=tmp_path)
    bad = run_checks.run_check({"id": "B", "what": "b", "command": "python3 -I checks/bad_check.py"}, root=tmp_path)
    assert ok["pass"] is True and bad["pass"] is False


def test_report_covers_every_check():
    out = run_checks.report(PLAN, root=ROOT)
    assert out["total"] == 4 and len(out["checks"]) == 4

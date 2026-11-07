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
        script = ROOT / check["command"].split()[-1]
        assert script.is_file(), check["command"]
        assert f'print("{check["id"]} pass")' in script.read_text(encoding="utf-8"), script


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
    with pytest.raises(ValueError):
        run_checks.load_block(block('{"checks": [{"id": "a-1", "what": "a", "command": "c"}]}'))


def test_only_safe_commands_run(tmp_path):
    result = run_checks.run_check({"id": "X", "what": "x", "command": "rm -rf /"}, root=tmp_path)
    assert result["pass"] is False and result["error"] == "command not allowed"


def _check(tmp_path, name, body):
    (tmp_path / "checks").mkdir(exist_ok=True)
    (tmp_path / "checks" / f"{name}.py").write_text(body)
    return {"id": "A", "what": "a", "command": f"python3 -I checks/{name}.py"}


def test_pass_needs_exit_zero_and_the_pass_line(tmp_path):
    assert run_checks.run_check(_check(tmp_path, "good", "print('A pass')\n"), root=tmp_path)["pass"] is True
    assert run_checks.run_check(_check(tmp_path, "bad", "raise SystemExit(1)\n"), root=tmp_path)["pass"] is False
    early = run_checks.run_check(_check(tmp_path, "early", "import os\nos._exit(0)\n"), root=tmp_path)
    assert early["pass"] is False and "pass line" in early["error"]


def test_only_one_check(tmp_path):
    out = run_checks.report(PLAN, root=ROOT, only="E1")
    assert out["total"] == 1 and out["checks"][0]["id"] == "E1"
    with pytest.raises(ValueError):
        run_checks.report(PLAN, root=ROOT, only="E9")


def test_report_covers_every_check():
    out = run_checks.report(PLAN, root=ROOT)
    assert out["total"] == 4 and len(out["checks"]) == 4

"""The demo control panel accepts its fixed actions and nothing else."""
from pathlib import Path

from dashboard import control as ctl


def test_only_fixed_actions_and_targets_pass():
    assert ctl.allowed("start", "sink") is None and ctl.allowed("drill", "llm") is None and ctl.allowed("reset", "oct_2023") is None
    for action, target in (("run", "sink"), ("start", "docker compose down"), ("drill", "rm -rf ."), ("start", None), (["start"], "sink"), ("replay", "sink")):
        assert ctl.allowed(action, target)


def test_a_bad_request_never_reaches_a_process():
    c = ctl.Control()
    assert c.act("start", "cmd.exe")[0] == 400 and c.job is None


def test_only_this_console_may_send_it():
    assert ctl.origin_ok({"X-DEWS-Control": "1"}, 8765)
    assert ctl.origin_ok({"X-DEWS-Control": "1", "Origin": "http://127.0.0.1:8765"}, 8765)
    assert not ctl.origin_ok({"X-DEWS-Control": "1", "Origin": "https://evil.example"}, 8765)
    assert not ctl.origin_ok({"Origin": "http://127.0.0.1:8765"}, 8765)


def test_commands_are_built_from_the_tables_and_rain_only_where_it_exists():
    drwt2 = ctl.job_commands("replay", "drwt2_2019", "py")
    assert len(drwt2) == 1 and drwt2[0][:3] == ["py", "-m", "producers.replay_producer"] and "08158700" in drwt2[0]
    oct23 = ctl.job_commands("replay", "oct_2023", "py")
    assert len(oct23) == 3 and "producers.mrms_qpe_producer" in oct23[0]      # rain first, or windows close without it
    assert ctl.job_commands("reset", "oct_2023", "py")[0][-1] == "--yes"
    assert ctl.job_commands("drill", "llm", "py") == [["py", "-m", "tools.fault_drill", "--scenario", "llm"]]


def test_process_detection_reads_the_right_command_line():
    lines = ["python -m storage.sink", "faust.exe -A streams.faust_app worker -l info --web-port 6067"]
    assert ctl.is_running(ctl.PROCESSES["sink"], lines) and ctl.is_running(ctl.PROCESSES["faust_6067"], lines)
    assert not ctl.is_running(ctl.PROCESSES["faust_6066"], lines) and not ctl.is_running(ctl.PROCESSES["notifier"], lines)


def test_no_compose_down_anywhere_in_the_panel():
    src = Path(ctl.__file__).read_text(encoding="utf-8")
    code = src.split('"""', 2)[2]
    assert "compose" not in code and "shell=True" not in code

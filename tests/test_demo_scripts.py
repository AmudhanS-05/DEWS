from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_stop_demo_keeps_the_data_and_matches_only_project_processes():
    s = (SCRIPTS / "stop_demo.ps1").read_text(encoding="utf-8")
    assert "docker compose stop" in s and "compose down" not in s.replace("# ", "") and "down -v" not in s
    assert "-m storage[.]sink" in s and "Stop-Process -Name" not in s      # by matched process id, never by bare name


def test_demo_scripts_are_ascii_for_windows_powershell_5_1():
    for name in ("start_demo.ps1", "stop_demo.ps1"):
        (SCRIPTS / name).read_text(encoding="utf-8").encode("ascii")       # no BOM: 5.1 would misread other characters


def test_start_demo_reads_the_venv_from_a_parameter_or_environment():
    s = (SCRIPTS / "start_demo.ps1").read_text(encoding="utf-8")
    assert "[string]$Venv = $env:DEWS_VENV" in s and "'.venv'" in s


def test_start_demo_starts_every_pipeline_process():
    s = (SCRIPTS / "start_demo.ps1").read_text(encoding="utf-8")
    for module in ("storage.sink", "streams.forecaster", "alerting.notifier", "dashboard.flow_monitor", "producers.usgs_iv_producer"):
        assert f"'{module}'" in s
    for port in (6066, 6067, 6068):
        assert f"'{port}'" in s

import io

from common.progress import Task, bar


def test_bar_bounds():
    assert bar(0, 10).endswith("  0%")
    assert bar(10, 10).endswith("100%")
    assert bar(99, 10).endswith("100%")
    assert bar(5, 0).endswith("100%")


def test_task_prints_start_progress_and_summary():
    out = io.StringIO()
    with Task("demo", total=4, unit="s", stream=out) as t:
        t.update(2, "halfway")
        t.summary("PASS: ok")
    text = out.getvalue()
    assert "[start" in text and "demo" in text
    assert "50%" in text and "halfway" in text
    assert "PASS: ok" in text and "elapsed" in text


def test_task_reports_failure():
    out = io.StringIO()
    try:
        with Task("boom", stream=out):
            raise ValueError("x")
    except ValueError:
        pass
    assert "FAILED" in out.getvalue()

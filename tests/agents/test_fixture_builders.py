"""The fixture builders write the tracked files only with --write; tests build into tmp_path (docs session, 31 Oct:
a full pytest run was reported to rewrite agentic_ai/fixtures/drwt2_2019-05_replay.json, only fetched_at changed)."""
import hashlib
import json

import pytest

from tests.agents import make_replay_fixture, record_fixture


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fake_fetch(site):
    """Synthetic gauge readings (15 min) 2019-05-01..06: flat, then a 6 h rise on 4 May, no network."""
    from datetime import datetime, timezone
    t0 = datetime(2019, 5, 1, tzinfo=timezone.utc).timestamp()
    out = []
    for i in range(5 * 96):
        t = t0 + i * 900
        h = (t - datetime(2019, 5, 4, tzinfo=timezone.utc).timestamp()) / 3600
        out.append((t, 2.0 + (min(max(h, 0), 6) * 2.5 if h > 0 else 0.0), False))
    return out


def test_replay_builder_writes_tmp_and_keeps_fetched_at_when_data_unchanged(tmp_path):
    pytest.importorskip("streams.forecaster")
    pytest.importorskip("ml.predict")
    tracked = make_replay_fixture.OUT
    before = _sha(tracked)
    out = make_replay_fixture.main(out=tmp_path / "replay.json", fetch_fn=_fake_fetch)
    assert out == tmp_path / "replay.json" and out.exists()
    first = out.read_bytes()
    d = json.loads(first)
    d["fetched_at"] = "2000-01-01T00:00:00Z"                     # pretend an older fetch of the same record
    out.write_text(json.dumps(d, indent=0) + "\n", encoding="utf-8")
    make_replay_fixture.main(out=out, fetch_fn=_fake_fetch)
    assert json.loads(out.read_bytes())["fetched_at"] == "2000-01-01T00:00:00Z"   # same data: no churn
    assert _sha(tracked) == before


def test_builders_default_to_a_temp_file_not_the_tracked_one(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    tracked = record_fixture.OUT
    before = _sha(tracked)
    out = record_fixture.main()
    assert out.parent == tmp_path and json.loads(out.read_text(encoding="utf-8"))["scenarios"]["normal"]["events"]
    assert _sha(tracked) == before


def test_tracked_replay_fixture_matches_git():
    """The tracked fixture is the committed measured record (no stray rewrite since checkout)."""
    import subprocess
    r = subprocess.run(["git", "diff", "--quiet", "--", str(make_replay_fixture.OUT)],
                       cwd=make_replay_fixture.OUT.parents[2], capture_output=True)
    if r.returncode not in (0, 1):
        pytest.skip("git not available")
    assert r.returncode == 0, "agentic_ai/fixtures/drwt2_2019-05_replay.json differs from git"

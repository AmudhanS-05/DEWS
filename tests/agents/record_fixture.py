"""Record agent_events fixtures for the core session's Agents tab:  python -m tests.agents.record_fixture --write
(without --write it writes to a temp file; tests never touch the tracked fixture).
Runs the graph (LLM_PROVIDER=none, in-memory data, no network) and saves agentic_ai/fixtures/agent_events_DRWT2.json
in the same shape as rows of the `agent_events` table (docs/CONTRACTS.md)."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

from agentic_ai import graph  # noqa: E402
from tests.agents.fake_tools import FakeTools  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "agentic_ai" / "fixtures" / "agent_events_DRWT2.json"
A1 = "08158700|minor|2019-05-04T03:00:00Z"
A2 = "08158700|moderate|2019-05-04T03:00:00Z"


def rows(res):
    return [{"event_id": i + 1, "run_id": res["run_id"], "alert_id": res["alert_id"],
             **{k: v for k, v in e.items() if k != "t_s"}} for i, e in enumerate(res["events"])]


def main(out: Path | None = None) -> Path:
    out = Path(out) if out is not None else Path(tempfile.gettempdir()) / "agent_events_DRWT2.json"
    t = FakeTools()
    scenarios = {"normal": graph.run_alert(t, A1), "update_2": graph.run_alert(t, A2),
                 "disagreement": graph.run_alert(FakeTools(flat=True), A1)}
    data = {"_note": "SIMULATION replay of DRWT2 May 2019 with illustrative seed values; LLM_PROVIDER=none (token_chunk "
                    "events are the deterministic text line by line). Same shape as agent_events rows.",
           "scenarios": {k: {"alert_id": v["alert_id"], "run_id": v["run_id"], "outcome": v["outcome"],
                             "confidence": v["confidence"], "advisory_text": v["draft"], "events": rows(v)}
                         for k, v in scenarios.items()}}
    out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print({k: len(v["events"]) for k, v in data["scenarios"].items()}, "->", out)
    return out


if __name__ == "__main__":
    main(OUT if "--write" in sys.argv[1:] else None)

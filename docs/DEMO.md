# Demo guide

How to start everything, what to show, what to say, and what to do if something breaks. Every scene below was run
twice in a row before it was written down.

## The story in one paragraph
Flash floods rise in hours, and the public river feed arrives about an hour late. Our pipeline streams live USGS
gauge readings and radar rainfall through Apache Kafka, builds hourly event-time windows in Faust, forecasts the river
stage a few hours ahead, and raises an alert when the forecast crosses an official NWS flood level. An alert becomes
a draft advisory within seconds; a team of agents then checks the evidence and rewrites it; a person approves it;
only then is it emailed, and every step is timed. We can kill any component and show the pipeline recover.

## Who uses it
| Who | Where they look | What they do |
|---|---|---|
| Duty officer (county emergency management, river authority; our stand-in: the team mailbox) | Map, Readings & alerts | sees each gauge against its official level; reads the advisory; approves, edits or rejects. Nothing is sent without approval. |
| Pipeline operator (us) | Pipeline flow, Metrics | checks every producer, consumer group and broker; reads latency and recovery times |
| Visitor or reviewer | all tabs, Power BI | live mode (real gauges, usually calm), then a replayed past flood marked SIMULATION, then a failure and its recovery |
The system supports officials; it is not an official warning authority.

## Starting everything
`scripts\start_demo.ps1` starts all of this (`scripts\stop_demo.ps1` stops it). By hand, in separate windows from the repo root with the venv active:
```powershell
cd docker; docker compose up -d; cd ..
python -m dashboard.flow_monitor                     # http://127.0.0.1:8765
python -m producers.usgs_iv_producer                 # live gauges
faust -A streams.faust_app worker -l info --web-port 6066   # and 6067, 6068
```

## Roles on demo day
One person narrates, one drives the console and terminals, one handles questions and the Power BI screen.

The demo is a replay with narration across Map, Pipeline flow and Agents.

## Scenes
(Each scene: what to run, what appears, what to say, fallback.)

**Scene 0 (available now): live pipeline and map.** Console → Pipeline flow: USGS live green, `gauge.usgs`
receiving. Map → 38 gauges, all below action stage (it is dry). Say: "Every dot is a real gauge in Texas, coloured
by its official NWS level. Readings arrive every 5 to 15 minutes but the feed sends them about hourly." Stop the live
producer (Control → Processes): after the stale threshold the node turns red and the change is logged. Without internet the map falls back
to the bundled world image by itself (gauges and alerts keep working; sharper tiles and terrain need the network).

**The demo path is the Control tab.** After `scripts\start_demo.ps1`, every step has a button:

| Step | Button |
|---|---|
| Clear a flood so it can run again (1.5 to 5 minutes, before the audience arrives) | Control → flood → Reset |
| Replay a flood: rain first, gauges 15 s later | Control → flood → Replay. It refuses dates already replayed since the last reset |
| Agents check and rewrite the drafts | Control → flood → Run agents |
| Approve, edit or reject a draft (sends the email) | Readings & alerts → Approve, then click again |
| Stop and restart a component | Control → Processes |
| Fault drill | Control → Fault drills |
| Run the acceptance checks | Control → Verify (stages S1 to S5) |

Never start the gauge replay by hand on dates already replayed: Faust's position is past them and the readings go to the dead-letter topic.

**Main scene: AHOT2 and LLAT2, 25 to 27 October 2023, with radar rain.** Control → first flood → Replay; about 3 minutes.
What appears, measured on two runs in a row: Pipeline flow goes green through replay, Faust, forecaster and sink; on the Map (Replay) the rain discs grow first (LLAT2's first rain is about 30 hours before its crossing); `s3_v0` raises a minor
alert with rule `alert_by_model` for LLAT2 in the 07:00 UTC window of 26 Oct and for AHOT2 in the 12:00 UTC window, each with a template draft in Readings & alerts.
The first reading at minor came at 10:20 UTC (LLAT2) and 15:25 UTC (AHOT2). Then Run agents and the Agents tab, then approve a draft. Say: "a forecast of crossing an official
category, in SIMULATION; about 2.3 to 2.4 hours gross and 1.3 to 1.4 hours net of the one-hour feed delay". Where the model's probability is above its threshold but its own stage forecast stays below minor, the draft and email say UNCERTAIN: read that out.
LLAT2 also raises a second alert on 27 Oct that is withdrawn: show it, it is a real false alarm. Use the Map's clock to step back through the rise.

**Acceptance check and the honest limit: DRWT2, 2 to 6 May 2019 (no radar rain, the archive starts in 2020).** Same pipeline, no alert: the river went from 3.05 ft to 8.16 ft
between one hourly window and the next, and the model's probability in the last window below minor was 0.546, under its threshold of 0.827. Say: "this one was too fast to
warn with an hourly feed. We report it; we did not tune the threshold to catch it."

## Demo checklist

**The day before:**
- [ ] `python -m pytest -q` green; `python -m tools.verify_slice S1` (then S2 to S5) read through.
- [ ] Cached gauge files in `data/raw/usgs/` and replay dates tried once: main scene `AHOT2`, `LLAT2` 2023-10-25..27 with rain (needs internet for the radar files); `08158700` 2019-05-02..06.
- [ ] Groq key in `.env` and a real agent run; Ollama model pulled as the fallback (`qwen2.5:7b`).
- [ ] Power BI: `dashboard/powerbi/DEWS.pbip` opens, password entered, pages built.

**Before the audience arrives:**
- [ ] `scripts\start_demo.ps1`; console at http://127.0.0.1:8765, all chips green.
- [ ] Clear the floods to be shown, Reset on the Control tab or `python -m tools.reset_replay` (1.5 to 5 minutes each: do it before the audience arrives).
- [ ] Laptop on mains, sleep off, notifications off, clock synced.

**During the demo:** replay plus narration across Map, Pipeline flow and Agents, main scene first. Run the replay, watch Pipeline flow, click the alert line to fly to the
gauge, step through it with the Map's clock, approve a draft in Readings & alerts. Then DRWT2 as the rise too fast to warn. Say "forecast of crossing an official category", and that replayed data is SIMULATION.

**Fallbacks:**
| If this fails | Do this |
|---|---|
| No internet | The Map falls back to the bundled NASA image by itself (`?offline` forces it); terrain and sharp tiles are gone, gauges and alerts stay. Replays run from cached files; live feeds go stale and the console shows it |
| Groq down or no key | Falls back to `LLM_FALLBACK`, then template v1 stays and nothing is blocked (`python -m tools.fault_drill --scenario llm`). Ollama needs `LLM_PROVIDER=ollama` |
| Replay shows nothing, dead letters rise | The dates were replayed already: run `tools.reset_replay` for them |
| Email cannot be sent | Show the bulletin and the `alerts.approved` record; say it was not delivered |
| Power BI will not open | Use the console's Metrics tab; state the substitution |

## Questions to prepare for
Why US data (the only open source we found with live gauges, official flood levels and hourly radar, plus archives); why pooled evaluation; why Faust instead of Kafka Streams; what "forecast of crossing an official
category" means; lead time gross vs net of the feed delay; what happens if the LLM is down (the template draft stays).

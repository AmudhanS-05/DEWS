# Demo guide (grows one scene per slice)

Read this when you come back to the project: it says how to start everything, what to show, what to say, and what
to do if something breaks. The core session adds one scene at the end of each slice, after it has run twice.

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
| Reviewer / faculty | all tabs, Power BI | live mode (real gauges, usually calm), then a replayed past flood marked SIMULATION, then a failure and its recovery |
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

**Story mode is dropped (decided 1 Nov):** the demo is a replay plus narration across Map, Pipeline flow and Agents.

## Scenes
(Each scene: what to run, what appears, what to say, fallback. Added at the end of each slice.)

**Scene 0 (available now): live pipeline and map.** Console → Pipeline flow: USGS live green, `gauge.usgs`
receiving. Map → 38 gauges, all below action stage (it is dry). Say: "Every dot is a real gauge in Texas, coloured
by its official NWS level. Readings arrive every 5 to 15 minutes but the feed sends them about hourly." Stop the live
producer: after the stale threshold the node turns red and the change is logged. Without internet the map falls back
to the bundled world image by itself (gauges and alerts keep working; sharper tiles and terrain need the network).

## Demo checklist

**The day before:**
- [ ] `python -m pytest -q` green; `python -m tools.verify_slice S1` (then S2 to S5) read through.
- [ ] Cached gauge files in `data/raw/usgs/` and replay dates tried once: `08158700` 2019-05-02..06; `AHOT2`, `LLAT2` 2023-10-25..27 with rain.
- [ ] Groq key in `.env` and a real agent run; Ollama model pulled as the fallback (`qwen2.5:7b`).
- [ ] Power BI: `dashboard/powerbi/DEWS.pbip` opens, password entered, pages built.

**Before the audience arrives:**
- [ ] `scripts\start_demo.ps1`; console at http://127.0.0.1:8765, all chips green.
- [ ] Clear the flood to be shown: `python -m tools.reset_replay --site 08158700 --start 2019-05-02 --end 2019-05-06 --yes` (about a minute).
- [ ] Laptop on mains, sleep off, notifications off, clock synced.

**During the demo:** replay plus narration across Map, Pipeline flow and Agents. Run the replay, watch Pipeline flow, click the alert line to fly to the
gauge, step through it with the Map's clock, approve a draft in Readings & alerts. Say "forecast of crossing an official category", and that replayed data is SIMULATION.

**Fallbacks:**
| If this fails | Do this |
|---|---|
| No internet | The Map falls back to the bundled NASA image by itself (`?offline` forces it); terrain and sharp tiles are gone, gauges and alerts stay. Replays run from cached files; live feeds go stale and the console shows it |
| Groq down or no key | Falls back to `LLM_FALLBACK`, then template v1 stays and nothing is blocked (`python -m tools.fault_drill --scenario llm`). Ollama needs `LLM_PROVIDER=ollama` |
| Replay shows nothing, dead letters rise | The dates were replayed already: run `tools.reset_replay` for them |
| Email cannot be sent | Show the bulletin and the `alerts.approved` record; say it was not delivered |
| Power BI will not open | Use the console's Metrics tab; state the substitution |

## Questions to prepare for
Why US data (only open source with live gauges, official levels and hourly radar, plus archives; India study shows
what is missing); why pooled evaluation; why Faust instead of Kafka Streams; what "forecast of crossing an official
category" means; lead time gross vs net of the feed delay; what happens if the LLM is down (the template draft stays).

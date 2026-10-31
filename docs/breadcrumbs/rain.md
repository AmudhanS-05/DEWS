# Rain breadcrumbs (cloud session on claude/rain; core merges these lines into BREADCRUMBS.md)

## Run locally (Windows laptop, PowerShell, from the repo folder)
```
..\spaenv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # adds numpy, eccodes, eccodeslib via requirements.txt (C2.1)
python -c "import eccodes; print(eccodes.codes_get_api_version())"   # expect 2.x; no separate ecCodes install
cd docker; docker compose up -d; cd ..        # rain.mrms and replay.rain already exist in topic-init

python -m producers.mrms_qpe_producer --probe                          # newest hour + latency per product
python -m producers.mrms_qpe_producer --once                           # one live poll: 38 records + 38 heartbeats
python -m producers.mrms_qpe_producer                                  # live, every 120 s (Ctrl-C stops)
python -m producers.mrms_qpe_producer --replay --start 2024-06-01T01 --end 2024-06-01T06 --sites 08158700
python -m producers.mrms_qpe_producer --replay --start 2024-05-31 --end 2024-06-01 --speed 3600   # all 38 gauges
python -m pytest -q tests/test_mrms_producer.py
```
Packages added (cloud session): `eccodes` 2.50.0, `eccodeslib` 2.50.0.33 (ships the ecCodes binary), `numpy` (2.5.3
here). All three are now in `requirements.txt`; `requirements-ml.txt` already listed eccodes/eccodeslib. On Windows,
if `import eccodes` fails, fall back to the Linux container named in BUILD_PHASES Phase 2b.

## Lines
[rain] C2.1 producer built: `producers/mrms_qpe_producer.py` (live -> rain.mrms LIVE, `--replay` -> replay.rain SIMULATION) + `common/mrms.py` (bucket keys, in-memory eccodes decode, circle indices/mean moved out of ml/mrms_history.py with the same names and signatures, so ml can import them instead of keeping its own copy). Contract for rain.mrms / replay.rain updated in docs/CONTRACTS.md (rain_mm_5km/10km/25km, coverage, n_cells, hour_start/hour_end, rain_id, fetched_ts, produced_ts, feed_delay_s LIVE only; S2 names window_end and rain_mm kept, rain_mm = 10 km).
[rain] Tests: 15 in tests/test_mrms_producer.py (decode + circle on a tiny synthetic GRIB2 written by eccodes, -3/9999 excluded, record schema with no NaN, rain_id idempotent and mode-specific, HourTracker new/duplicate/revision + state round trip, backfill cap, archive start, replay pacing 00Z..05Z at 3600x = 5.0 s with a missing hour, live poll with a fake bucket, error -> deadletter + heartbeats). pytest -q: 256 passed, 26 skipped (PostgreSQL tests, no DSN in the cloud), Python 3.13.
[rain] Latency measured 31 Oct 2026 (`--probe`, S3 LastModified - valid time, last 6 hourly files): RadarOnly_QPE_01H at HH:00 2.7-3.0 min, MultiSensor Pass 1 15.3-15.6 min, Pass 2 56.9-57.5 min. Live default = radaronly (lead time; not gauge-corrected, while ML v1 is trained on Pass 2 - state it); `--product pass1` for the gauge-corrected one.
[rain] Real live poll (cloud, 17:43Z, published to an in-memory FAKE producer, not Kafka): newest hour 17Z, 38 rain records + 38 heartbeats in 1.8 s (radaronly) / 2.4 s (pass1); 10 km circle 293 cells at Austin; all 0.0 mm (dry hour). The 43 min feed delay of that first poll is only because it ran at :43; steady-state delay = publish latency + up to one poll interval (120 s), see next line.
[rain] Real replay (cloud, FAKE producer; a local broker could not be started: Docker Hub 429 and a Kafka binary launch was not permitted): DRWT2 08158700, Pass 2 2024-06-01T01Z..06Z (before its 06:40Z minor crossing), 6/6 hours, speed 3600x, 5.6 s wall, behind schedule 1 h (downloads ~0.5-1.5 s per file with 4 workers, so 3600x is at the limit; use --speed 1800 for steady pacing): 10 km mean 0.00, 0.68, 7.56, 0.00, 0.00, 0.00 mm (5 km 9.13, 25 km 2.85 at 03Z), 294 cells, coverage 1.0. Matches ml's M2.1 value (7.56 mm, 294 cells) for the same hour.
[rain] No radar rain for the 2019 demo flood: the noaa-mrms-pds archive starts 2020-10-14; `--replay` over 2019 prints the skipped hours and publishes nothing. Rain demo needs a post-2020 flood (e.g. DRWT2 2024-06-01).

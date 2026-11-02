# Test fixtures

| File | What it is | Real? |
|---|---|---|
| `usgs_iv_reconstructed.json` | USGS IV response in WaterML-JSON layout | **Values and timestamps are real** (fetched 30 Sep 2026 for 09510200 and 07332500). The **layout** is reconstructed from USGS's documented format because the fetch tool reformatted the raw JSON. The second "backup" series for 09510200 uses placeholder values (1.76/1.77) only to test the two-series case. One `-999999` point is added to test noDataValue handling. |
| `nwps_gauge_reconstructed.json` | NWPS gauge metadata | **Values are real** (TSGA2, fetched 30 Sep 2026); **layout is a best guess**. |
| `live_usgs_iv.json`, `live_nwps_gauge.json` | Raw responses saved by `python -m tools.fetch_sample` | Real. Not committed until you run it; when present, `pytest` runs the parsers against them too. |

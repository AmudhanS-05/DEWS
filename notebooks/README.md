# EDA notebooks (for submission)

Self-contained: each notebook downloads its own data, so it runs in **Google Colab** or
**Kaggle** (Kaggle: Settings → Internet → On). Edit the config cell, then *Run all*.

| Notebook | Dataset | What it covers |
|---|---|---|
| `01_classic_eda_usgs.ipynb` | USGS Instantaneous Values (stage `00065`, discharge `00060`) | structure, missing values / no-data codes / duplicates, qualifiers (provisional vs approved), reporting interval and gaps, monthly completeness heatmap, sensor-series agreement, distributions and skew, time series and peak zoom, monthly and hourly seasonality, rating curve, cross-gauge correlation, ACF/PACF, ADF stationarity, rolling statistics, outliers and rate-of-change spikes |
| `02_flood_specific_eda.ipynb` | USGS IV + NOAA NWPS flood categories + Open-Meteo hourly rainfall | % of time above each official category, stage-duration curve, flood events (same definitions as the pipeline's gate check), events per year / month, rise time (lead-time ceiling), aligned event hydrographs, rise rates before floods, live feed delay vs rise time, rain → stage lag, persistence and rise-rate baseline scores, PASS/FAIL gate summary |
| `03_mrms_rainfall_eda.ipynb` | NOAA MRMS QPE (AWS public bucket) | products and cadence, file sizes, publication delay, one decoded grid (missing codes, distribution), rainfall map and time series around a gauge during an event, rain vs stage |

Order: run 02 first to find real flood events, then put one event's time into 03.

**Tested how:** all three were executed end to end here against simulated USGS, NWPS,
Open-Meteo and MRMS responses (the build environment cannot reach those services).
The USGS parsing was also checked against a real response saved on 2 Oct 2026. Not
tested here: real GRIB2 decoding by `cfgrib` in notebook 03 — if it fails in Colab,
check the `!pip install` cell ran and restart the runtime once.

Outputs (CSV summaries) are written to `data/` next to the notebook.

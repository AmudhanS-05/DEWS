| Property | US (USGS IV) | US source | India measured | India literature | India source |
|---|---|---|---|---|---|
| Reporting interval (median per station) | 5 min (range 5 to 15) | measured, india.audit on 38 USGS gauges | pending (India-WRIS downloads) | daily | INDIA_STUDY.md (India-WRIS), to confirm with the downloads |
| Publication delay | 24 to 69 min (one live sample) | measured once, FAQ.md (EDA, 3 Oct 2026) | not measurable from archive files | daily values published after the day; no live feed documented | INDIA_STUDY.md |
| Archive length | 11.0 years (range 9.2 to 11.0) | measured, india.audit on 38 USGS gauges | pending (India-WRIS downloads) | multi-year; one year per download request | INDIA_STUDY.md |
| Missing data vs the station's own interval | 0.8 % (range 0.0 to 36.6) | measured, india.audit on 38 USGS gauges | pending (India-WRIS downloads) | gaps reported | CAMELS-IND, ESSD 2025 |
| Longest gap | 120 h (range 2 to 3276) | measured, india.audit on 38 USGS gauges | pending (India-WRIS downloads) |  |  |
| Official thresholds machine-readable | yes: NWPS API, numeric categories for 38 gauges in config/gauge_locations.csv | source (NWPS API) | pending (India-WRIS downloads) | warning / danger level / HFL in CWC web pages and daily PDF bulletins; no open API found | INDIA_STUDY.md |
| Measurement method | automatic sensors (USGS IV gage height) | source | pending (India-WRIS downloads) | manual staff-gauge readings, about daily (00:00 / 08:00): description 'MANUAL-Water Level by Staff Gauge' at Aheri (Gudem Bridge, Pranhita) and NEELEESWARAM (Periyar) | observed in two India-WRIS API responses (--discover, 31 Oct 2026); measured per station once fetched |
| Access method | REST API (JSON), no key | source | pending (India-WRIS downloads) | manual download, one year per Excel file; POST API (OpenAPI spec, no auth) | INDIA_STUDY.md; docs/indiawris_api.json |
| Share of stations free to use | all USGS IV data are public | source terms |  | 645 of 4,824 listed gauges (Peninsular) | CAMELS-IND paper, ESSD 2025 |
| Events above the threshold (US: NWPS minor; India: CWC danger level) | 310 total, median 5 (range 1 to 46) per station | measured, india.audit on 38 USGS gauges | pending (India-WRIS downloads) |  | needs downloads + india/thresholds.csv |
| Rise time resolvable (interval < median rise) | 38 of 38 stations with events | measured, india.audit on 38 USGS gauges | pending (India-WRIS downloads) | expected no with daily data (to be confirmed) | INDIA_STUDY.md step B |

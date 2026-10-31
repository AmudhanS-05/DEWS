"""Candidate demo floods with radar rain: every minor crossing from 2020-10-14 on (MRMS archive start), with rise time,
MRMS rain before it (10 km circle) and whether the ACTIVE forecaster (s3_v0, 3 h) and the rise-rate rule alerted
before it (ml.harness alert rule: below minor, 6 h cooldown, alert in [crossing - 3 h, crossing)) and the lead.
Period: 2024+ = test (never used for any choice); 2021-2023 = validation (v0's threshold was chosen there);
before 2021 = v0 training. Uses the local S3 frame (python -m ml.s3_model build).

    python -m ml.demo_floods --out ml/figures/demo_flood_candidates.csv
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np


def main(argv=None):
    import joblib
    import pandas as pd
    from ml.mrms_history import ARCHIVE_START
    from ml.s3_model import CROSSINGS, FRAME, fast_alerts, rise_rate_fire
    p = argparse.ArgumentParser()
    p.add_argument("--h", type=int, default=3)
    p.add_argument("--out", default="ml/figures/demo_flood_candidates.csv")
    a = p.parse_args(argv)
    h = a.h
    info = json.load(open(CROSSINGS, encoding="utf-8"))
    m = joblib.load(f"ml/models/s3_v0_h{h}.joblib")
    df = pd.read_parquet(FRAME)
    rain = pd.read_parquet("ml/rain_cache/mrms_circle.parquet")
    rain = rain[rain.radius_km == 10.0]
    rows = []
    for site, g in df.groupby("site_no", sort=True):
        cr = [c for c in info[site]["crossings"] if c[0] >= ARCHIVE_START + 48 * 3600]
        if not cr:
            continue
        g = g.sort_values("t")
        t, cur, thr = g.t.to_numpy(), g.stage.to_numpy(), info[site]["thr"]
        p0 = m["clf"].predict_proba(g[m["features"]])[:, 1]
        al_v0 = [x[0] for x in fast_alerts(t, cur, thr, p0 >= m["tau"])]
        al_rr = [x[0] for x in fast_alerts(t, cur, thr, rise_rate_fire(g, h, info)[0])]
        r = rain[rain.site_no == site].set_index("hour_ts").rain_mm
        for ct, _, slow in cr:
            def lead(al):
                prior = [x for x in al if ct - h * 3600 <= x < ct]
                return round((ct - min(prior)) / 3600, 2) if prior else None
            hrs = [np.floor(ct / 3600) * 3600 - k * 3600 for k in range(0, 48)]
            r24 = r.reindex(hrs[:24])
            r48 = r.reindex(hrs)
            k = np.searchsorted(t, ct)
            peak = float(g.stage.iloc[k:k + 24].max()) if k < len(g) else None
            yr = datetime.fromtimestamp(ct, timezone.utc).year
            rows.append({"lid": info[site]["lid"], "site_no": site,
                         "crossing_utc": datetime.fromtimestamp(ct, timezone.utc).strftime("%Y-%m-%d %H:%M"),
                         "period": "test" if yr >= 2024 else ("validation" if yr >= 2021 else "train"),
                         "slow_rise_ge_3h": bool(slow), "minor_ft": thr, "peak_next_24h_hourly_last_ft": peak,
                         "rain_24h_mm": round(float(r24.sum()), 1) if r24.notna().any() else None,
                         "rain_48h_mm": round(float(r48.sum()), 1) if r48.notna().any() else None,
                         "rain_hours_known_48h": int(r48.notna().sum()),
                         "v0_alert_lead_h": lead(al_v0), "riserate_alert_lead_h": lead(al_rr)})
    out = pd.DataFrame(rows).sort_values("crossing_utc")
    out.to_csv(a.out, index=False)
    print(f"done: {len(out)} crossings since 2020-10-16 -> {a.out}")
    print(out.to_string(index=False, max_rows=200))


if __name__ == "__main__":
    main()

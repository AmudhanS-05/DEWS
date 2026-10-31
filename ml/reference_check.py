"""Diagnostic: re-implements notebook 02's baseline_scores (rise-rate, 3 h) on the cached history to explain the FAQ
reference F1s. It scores hourly MEANS labelled at the START of each hour (pandas' default, label="left": the alert
at 06:00 has already seen data up to 07:00) and at the END of the hour (label="right", no look-ahead).
Not a model and not a result to report as skill; it shows where the reference bar comes from.

    python -m ml.reference_check            # notebook window: 10 years ending 2026-10-02
"""
import numpy as np, pandas as pd

from producers.replay_producer import load_readings
from ml.dataset import primary_series
from common import hydro

G = {"08158700": 5.5, "08172400": 14.0, "08150000": 16.0, "08156800": 7.0}


names = {"08158700": "DRWT2", "08172400": "LCPT2", "08150000": "JNCT2", "08156800": "AHOT2"}


def main():
    for s, thr in G.items():
        rs = primary_series(load_readings(s, "2016-10-03", "2026-10-02"))
        ser = sorted((r["reading_ts"], r["value"]) for r in rs)
        v = pd.Series([x for _, x in ser], index=pd.to_datetime([t for t, _ in ser], unit="s"))
        cr = [pd.Timestamp(ser[i][0], unit="s") for i in hydro.find_crossings(ser, thr)]
        out = []
        for label in ("left", "right"):
            h = v.resample("h", label=label, closed="left").mean().interpolate(limit=3).dropna()
            fc = h + h.diff().clip(lower=0) * 3
            alerts, last = [], None
            for t in h.index:
                if fc[t] >= thr > h[t] and (last is None or t - last >= pd.Timedelta(hours=6)):
                    alerts.append(t); last = t
            H = pd.Timedelta(hours=3)
            tp = sum(any(a < c <= a + H for c in cr) for a in alerts)
            det = sum(any(c - H <= a < c for a in alerts) for c in cr)
            p = tp / len(alerts) if alerts else 0; r = det / len(cr)
            out.append(f"{label}: F1={2*p*r/(p+r) if p+r else 0:.2f} (alerts {len(alerts)}, det {det}/{len(cr)})")
        print(names[s], " | ".join(out))


if __name__ == "__main__":
    main()

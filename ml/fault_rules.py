"""Physical sensor-fault rules (team decision 31 Oct 2026: replaces the Isolation Forest for display).

Rules on raw readings of one gauge, with nearby gauges (within NEIGHBOR_KM, from config/gauge_locations.csv) and MRMS
rain (10 km circle, where known) as independent evidence. Each returns flag times (when the rule can decide, i.e.
causal with a delay of a few readings):
  no_readings  >= GAP_H hours without readings                                            (flag at gap start + GAP_H)
  spike        a jump of >= a*s from the previous reading that returns (to within 25 % of the jump) within 1 or 2
               readings                                                               (flag at the returning reading)
  stuck        the identical value for >= N hours while a nearby gauge's hourly stage moves >= NB_MOVE_FT in the same
               period, or >= RAIN_MM of rain fell (where rain is known)              (flag at run start + N h)
  step         a jump of >= d*s that then holds (next STEP_HOLD readings within 15 % of the jump, previous ones within
               25 %),
               with independent evidence of no event: rain known and < 1 mm in the 6 h before, or at least one nearby
               gauge and none of them moving >= NB_MOVE_FT within 1 h before to 2 h after
                                                                         (flag at the STEP_HOLD-th reading after)
s = the gauge's 99th percentile of |hourly change| on training data (ml.anomaly scales).
Thresholds a, N, d are chosen on validation years (2021-2023) as the most sensitive setting whose false-flag rate on
real flood windows (12 h before to 6 h after a minor crossing) is <= 1 % and clean flag episodes <= 5 per gauge-year;
the test years (2024-2026) are then used once. A rule ships to the contract only if its TEST flood-window rate is
<= 1 % (team rule).

    python -m ml.fault_rules evaluate --period validation      # grid -> ml/figures/fault_rules_validation.csv
    python -m ml.fault_rules evaluate --period test            # chosen settings -> ml/figures/fault_rules_test.csv
"""
from __future__ import annotations

import csv
import json
import math
import os

import numpy as np

GAP_H = 3.0
NEIGHBOR_KM = 60.0
NB_MOVE_FT = 0.1
RAIN_MM = 5.0
STEP_HOLD = 4
GRID = {"spike": [1.0, 2.0, 3.0], "stuck": [6.0, 12.0, 24.0], "step": [1.0, 1.5, 2.0]}
RULES = ("no_readings", "spike", "stuck", "step")
PERIODS = {"validation": ("2021-01-01", "2023-12-31"), "test": ("2024-01-01", "2026-10-31")}


# ---------------- context: neighbours and rain ----------------

def neighbors(path: str = "config/gauge_locations.csv", km: float = NEIGHBOR_KM, k: int = 3) -> dict[str, list[str]]:
    with open(path, encoding="utf-8") as f:
        g = [(r["site_no"], float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)]
    out = {}
    for s, la, lo in g:
        d = []
        for s2, la2, lo2 in g:
            if s2 == s:
                continue
            x = math.radians(lo2 - lo) * math.cos(math.radians((la + la2) / 2))
            y = math.radians(la2 - la)
            d.append((6371.0 * math.hypot(x, y), s2))
        out[s] = [s2 for dist, s2 in sorted(d) if dist <= km][:k]
    return out


class Context:
    """Hourly stage of every gauge (from the S3 frame) and 10 km MRMS rain (where known)."""
    def __init__(self, hourly: dict[str, tuple[np.ndarray, np.ndarray]], rain: dict[str, dict[float, float]],
                 nbrs: dict[str, list[str]]):
        self.hourly, self.rain, self.nbrs = hourly, rain, nbrs

    @classmethod
    def from_windows(cls, windows_by_site: dict[str, list[dict]], rain_by_site: dict[str, dict] | None = None,
                     nbrs: dict[str, list[str]] | None = None) -> "Context":
        """Live use: recent aggregates.window records per gauge (stage_last, window_start_ts) and, optionally,
        hourly rain per gauge ({hour_end_ts: rain_mm}, e.g. from rain.mrms)."""
        hourly = {}
        for site, ws in windows_by_site.items():
            ws = sorted((w for w in ws if w.get("stage_last") is not None), key=lambda w: w["window_start_ts"])
            hourly[site] = (np.array([float(w["window_start_ts"]) + 3600 for w in ws]),
                            np.array([float(w["stage_last"]) for w in ws]))
        return cls(hourly, {k: {float(h): float(v) for h, v in r.items()} for k, r in (rain_by_site or {}).items()},
                   nbrs if nbrs is not None else neighbors())

    def nb_moves(self, site: str, a: float, b: float) -> tuple[int, int]:
        """(neighbours with data in [a, b], neighbours whose hourly stage range in [a, b] is >= NB_MOVE_FT)."""
        have = moving = 0
        for n in self.nbrs.get(site, []):
            if n not in self.hourly:
                continue
            t, v = self.hourly[n]
            lo, hi = np.searchsorted(t, a), np.searchsorted(t, b, side="right")
            seg = v[lo:hi]
            seg = seg[np.isfinite(seg)]
            if len(seg) >= 1:
                have += 1
                if len(seg) >= 2 and seg.max() - seg.min() >= NB_MOVE_FT:
                    moving += 1
        return have, moving

    def rain_sum(self, site: str, a: float, b: float) -> float | None:
        """Sum of hourly rain with valid time in (a, b]; None unless every hour is known."""
        r = self.rain.get(site)
        if not r:
            return None
        h0 = math.floor(a / 3600) * 3600 + 3600
        hrs = np.arange(h0, b + 1, 3600.0)
        vals = [r.get(float(h)) for h in hrs]
        if not len(vals) or any(x is None for x in vals):
            return None
        return float(np.sum(vals))


# ---------------- rules (vectorised where possible) ----------------

def rule_gap(t: np.ndarray, v: np.ndarray, gap_h: float = GAP_H) -> list[float]:
    d = np.diff(t)
    return [float(t[i] + gap_h * 3600) for i in np.nonzero(d >= gap_h * 3600)[0]]


def rule_spike(t: np.ndarray, v: np.ndarray, thr: float) -> list[float]:
    if len(v) < 4:
        return []
    d = np.diff(v)                                   # d[i-1] = v[i] - v[i-1]
    out = []
    for i in np.nonzero(np.abs(d) >= thr)[0] + 1:   # reading i jumped
        if i + 1 >= len(v):
            continue
        jump = v[i] - v[i - 1]
        for k in (1, 2):
            if i + k < len(v) and abs(v[i + k] - v[i - 1]) <= 0.25 * abs(jump):
                out.append(float(t[i + k]))
                break
    return out


def rule_stuck(t: np.ndarray, v: np.ndarray, site: str, ctx: Context, n_h: float) -> list[float]:
    if len(v) < 2:
        return []
    change = np.concatenate([[True], v[1:] != v[:-1]])
    starts = np.nonzero(change)[0]
    ends = np.concatenate([starts[1:], [len(v)]]) - 1
    out = []
    for s_, e_ in zip(starts, ends):
        a, b = t[s_], t[e_]
        if b - a < n_h * 3600:
            continue
        dec = a + n_h * 3600
        have, moving = ctx.nb_moves(site, a, dec)
        rain = ctx.rain_sum(site, a, dec)
        if moving > 0 or (rain is not None and rain >= RAIN_MM):
            out.append(float(dec))
    return out


def rule_step(t: np.ndarray, v: np.ndarray, site: str, ctx: Context, thr: float) -> list[float]:
    if len(v) < 2 * STEP_HOLD + 2:
        return []
    d = np.diff(v)
    out = []
    for i in np.nonzero(np.abs(d) >= thr)[0] + 1:
        if i < STEP_HOLD or i + STEP_HOLD >= len(v):
            continue
        jump = v[i] - v[i - 1]
        before, after = v[i - STEP_HOLD:i], v[i:i + STEP_HOLD + 1]
        if np.ptp(before) > 0.25 * abs(jump) or np.ptp(after) > 0.15 * abs(jump):
            continue
        rain = ctx.rain_sum(site, t[i] - 6 * 3600, t[i])
        have, moving = ctx.nb_moves(site, t[i] - 3600, t[i] + 2 * 3600)
        quiet = (rain is not None and rain < 1.0) or (have > 0 and moving == 0)
        if quiet and moving == 0 and not (rain is not None and rain >= 1.0):
            out.append(float(t[i + STEP_HOLD]))
    return out


def run_rules(t: np.ndarray, v: np.ndarray, site: str, ctx: Context, scale: float, settings: dict) -> dict:
    """{rule: flag times}; settings: {"spike": a, "stuck": N_h, "step": d}."""
    return {"no_readings": rule_gap(t, v),
            "spike": rule_spike(t, v, settings["spike"] * scale),
            "stuck": rule_stuck(t, v, site, ctx, settings["stuck"]),
            "step": rule_step(t, v, site, ctx, settings["step"] * scale)}


def check_readings(readings: list[dict], site: str, ctx: Context | None, scale: float, settings: dict) -> dict:
    """Contract-style entry point on the recent readings of one gauge (oldest first): {suspect, reasons, flags}."""
    t = np.array([float(r["reading_ts"]) for r in readings])
    v = np.array([float(r["value"]) for r in readings])
    ctx = ctx or Context({}, {}, {})
    fl = run_rules(t, v, site, ctx, scale, settings)
    reasons = [k for k, x in fl.items() if x]
    return {"suspect": bool(reasons), "reasons": reasons, "flags": fl}


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "models", "fault_rules.json")
_CFG: dict = {}


def check_gauge(readings: list[dict], site_no: str, ctx: Context | None = None, config: dict | None = None) -> dict:
    """CONTRACT (proposed): SHIPPED rules only, on the gauge's recent readings (oldest first, at least the last
    48 h). Returns {suspect, reasons, flags}: reasons from no_readings / stuck / step; flags = {rule: [flag_ts]}.
    Never raises (returns suspect False, reasons ["unavailable"] on any error)."""
    try:
        cfg = config or _CFG.setdefault("cfg", json.load(open(CONFIG_PATH, encoding="utf-8")))
        scale = float(cfg["scales"].get(str(site_no), cfg["default_scale"]))
        out = check_readings(readings, str(site_no), ctx, scale, cfg["settings"])
        fl = {k: v for k, v in out["flags"].items() if k in cfg["shipped"]}
        reasons = [k for k, v in fl.items() if v]
        return {"suspect": bool(reasons), "reasons": reasons, "flags": fl}
    except Exception:  # noqa: BLE001
        return {"suspect": False, "reasons": ["unavailable"], "flags": {}}


# ---------------- evaluation ----------------

def _hours(times) -> set:
    return {math.floor(x / 3600) for x in times}


def evaluate_period(period: str, settings_list: list[dict], per_gauge: int = 10, seed: int = 1, progress=None,
                    scales: dict | None = None, ctx: Context | None = None, gauges=None) -> list[dict]:
    from common import hydro
    from ml.anomaly import FAULTS, inject
    prog = progress or (lambda it, d, total=None: it)
    start, end = PERIODS[period]
    rng = np.random.default_rng(seed)
    acc = [{"settings": st, "fault": {f: [0, 0, []] for f in FAULTS}, "by_rule": {f: {} for f in FAULTS},
            "clean": {r: 0 for r in RULES}, "flood": {r: 0 for r in RULES}, "flood_any": {r: 0 for r in RULES}}
           for st in settings_list]
    tot = {"gauge_years": 0.0, "flood_windows": 0, "flood_events": 0}
    for g in prog(gauges, "gauges"):
        from ml.dataset import primary_series
        rs = primary_series(g["readings"])
        del g["readings"]
        site = g["site_no"]
        t = np.array([r["reading_ts"] for r in rs], dtype=float)
        v = np.array([r["value"] for r in rs], dtype=float)
        if len(t) < 100:
            continue
        s = scales.get(site, (0.1,))[0]
        ser = list(zip(t, v))
        cr = [ser[i][0] for i in hydro.find_crossings(ser, g["cats"]["minor"])]
        fh = set()
        for c in cr:
            fh |= set(range(math.floor((c - 12 * 3600) / 3600), math.floor((c + 6 * 3600) / 3600) + 1))
        tot["gauge_years"] += (t[-1] - t[0]) / (365.25 * 86400)
        tot["flood_windows"] += len(fh)
        tot["flood_events"] += len(cr)
        act = g["cats"]["action"]
        quiet = np.nonzero((v < act) & (np.arange(len(v)) > 48) & (np.arange(len(v)) < len(v) - 2000))[0]
        picks = {f: [int(quiet[int(rng.integers(len(quiet)))]) for _ in range(per_gauge)] if len(quiet) else []
                 for f in FAULTS}
        injected = {}
        for f in FAULTS:                                 # same faults for every setting
            for i0 in picks[f]:
                t0 = t[i0]
                lo, hi = np.searchsorted(t, t0 - 48 * 3600), np.searchsorted(t, t0 + 100 * 3600)
                seg = rs[lo:hi]
                bad, t1 = inject(seg, f, t0, s, rng)
                injected.setdefault(f, []).append((seg, bad, t0, t1))
        for a in acc:
            st = a["settings"]
            fl = run_rules(t, v, site, ctx, s, st)
            for r, times in fl.items():
                hrs = sorted(_hours(times))
                a["clean"][r] += sum(1 for k, h in enumerate(hrs) if k == 0 or h - hrs[k - 1] > 1)   # episodes
                a["flood"][r] += len(_hours(times) & fh)
                for c in cr:
                    if any(c - 12 * 3600 <= x <= c + 6 * 3600 for x in times):
                        a["flood_any"][r] += 1
            for f, items in injected.items():
                for seg, bad, t0, t1 in items:
                    ta = np.array([r["reading_ts"] for r in seg]); va = np.array([r["value"] for r in seg])
                    tb = np.array([r["reading_ts"] for r in bad]); vb = np.array([r["value"] for r in bad])
                    ca, cb = run_rules(ta, va, site, ctx, s, st), run_rules(tb, vb, site, ctx, s, st)
                    hi_t = t1 + 2 * 3600 if f in ("spike", "dropout") else t1
                    exc = sorted((x, r) for r in RULES for x in cb[r]
                                 if t0 <= x <= hi_t and math.floor(x / 3600) not in _hours(ca[r]))
                    a["fault"][f][0] += 1
                    if exc:
                        a["fault"][f][1] += 1
                        a["fault"][f][2].append((exc[0][0] - t0) / 3600)
                        a["by_rule"][f][exc[0][1]] = a["by_rule"][f].get(exc[0][1], 0) + 1
    rows = []
    for a in acc:
        st = a["settings"]
        row = {"period": period, "spike_a": st["spike"], "stuck_N_h": st["stuck"], "step_d": st["step"]}
        for f, (n, d, ttf) in a["fault"].items():
            row[f"{f}_detected"] = round(d / n, 3) if n else None
            row[f"{f}_ttf_h"] = round(float(np.median(ttf)), 2) if ttf else None
            row[f"{f}_by_rule"] = ";".join(f"{k}:{c}" for k, c in sorted(a["by_rule"][f].items()))
        for r in RULES:
            row[f"{r}_clean_episodes_per_gauge_year"] = round(a["clean"][r] / tot["gauge_years"], 2)
            row[f"{r}_flood_window_rate"] = round(a["flood"][r] / tot["flood_windows"], 4) if tot["flood_windows"] else None
            row[f"{r}_flood_events_touched"] = a["flood_any"][r]
        row["flood_windows"], row["flood_events"] = tot["flood_windows"], tot["flood_events"]
        rows.append(row)
    return rows


def build_context(start: str, end: str) -> Context:
    import pandas as pd
    from ml.s3_model import FRAME, ts
    f = pd.read_parquet(FRAME, columns=["site_no", "t", "stage"])
    f = f[(f.t >= ts(start) - 3 * 86400) & (f.t <= ts(end) + 3 * 86400)]
    hourly = {s: (g.t.to_numpy(), g.stage.to_numpy()) for s, g in f.sort_values("t").groupby("site_no")}
    rain = pd.read_parquet("ml/rain_cache/mrms_circle.parquet")
    rain = rain[rain.radius_km == 10.0]
    rmap = {s: dict(zip(g.hour_ts.astype(float), g.rain_mm.astype(float))) for s, g in rain.groupby("site_no")}
    return Context(hourly, rmap, neighbors())


def main(argv=None):
    import argparse
    import joblib
    from common import config
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--period", choices=sorted(PERIODS), required=True)
    e.add_argument("--gauges", default="config/gauge_locations.csv")
    e.add_argument("--per-gauge", type=int, default=10)
    e.add_argument("--scales-model", help="ml.anomaly model holding per-gauge scales (validation: one fit < 2021)")
    e.add_argument("--settings", default="ml/figures/fault_rules_settings.json",
                   help="test: chosen settings (written by the validation run)")
    a = p.parse_args(argv)
    timer = Timer()
    start, end = PERIODS[a.period]
    scales = joblib.load(a.scales_model or "ml/models/anomaly_if.joblib")["scales"]
    if a.period == "validation":
        # each rule's flood / clean rates depend only on its own threshold, so 3 aligned settings (loose -> strict)
        # cover the grid; fault detections are attributed to the rule that fired (columns *_by_rule)
        grid = [{k: GRID[k][i] for k in GRID} for i in range(3)]
    else:
        grid = [json.load(open(a.settings))["chosen"]]
    print(f"ml.fault_rules evaluate {a.period} {start}..{end}: {len(grid)} setting(s), {a.per_gauge} faults per type "
          f"per gauge, neighbours within {NEIGHBOR_KM:g} km")
    ctx = build_context(start, end)
    gauges = iter_gauges(a.gauges, None, start, end, config.DATA_DIR)
    rows = evaluate_period(a.period, grid, a.per_gauge, progress=lambda it, d, total=None:
                           bar(it, d, count_gauges(a.gauges, None)), scales=scales, ctx=ctx, gauges=gauges)
    out = f"ml/figures/fault_rules_{a.period}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    if a.period == "validation":
        chosen = {}
        for rule, key, col in (("spike", "spike_a", "spike"), ("stuck", "stuck_N_h", "flatline"),
                               ("step", "step_d", "step")):
            ok = [r for r in rows if (r[f"{rule}_flood_window_rate"] or 0) <= 0.01
                  and r[f"{rule}_clean_episodes_per_gauge_year"] <= 5]
            best = max(ok, key=lambda r: (r[f"{col}_detected"] or 0, -r[f"{rule}_clean_episodes_per_gauge_year"])) \
                if ok else None
            chosen[rule] = best[key] if best else max(GRID[rule])
            print(f"  {rule}: chosen {key}={chosen[rule]} ({'meets' if best else 'NO setting meets'} flood <= 1 % "
                  f"and clean <= 5/gauge-year on validation)")
        json.dump({"chosen": chosen, "criteria": "flood-window rate <= 1 % and clean episodes <= 5 per gauge-year "
                   "on 2021-2023; most sensitive (highest detection of its fault type)"}, open(a.settings, "w"),
                  indent=1)
    for r in rows if a.period == "test" else []:
        print(json.dumps(r, indent=1))
    print(f"done -> {out} in {timer.elapsed()}")


if __name__ == "__main__":
    main()

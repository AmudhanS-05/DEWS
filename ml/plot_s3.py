"""Figure: row-level precision-recall curves at 3 h on the TEST period (2024-01-01 onward), with operating points.

Row level = one hourly window, label y_cross_3 (stage below minor now, at or above minor within 3 h). The markers are
row-level points on the SAME rows: each model at its validation-chosen tau, and the rise-rate rule. Event-level
metrics (F1, false alarms, lead time) are in ml/figures/s3_eval.csv, not in this figure.
Needs the local outputs of `python -m ml.s3_model build` and `train` (s3_frame.parquet, s3_eval_scores.parquet).

    python -m ml.plot_s3 --out ml/figures/s3_pr_3h.png
"""
from __future__ import annotations

import argparse
import json

import numpy as np

# reference palette, categorical slots 1-3 (validated: scripts/validate_palette.js, light surface #fcfcfb);
# slot 3 is below 3:1 contrast, so every curve also has a line style and a direct label
C = {"s3_v0": "#2a78d6", "s3_v1": "#eb6834", "rise_rate": "#1baf7a"}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a64", "#e4e3de", "#fcfcfb"


def main(argv=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    from sklearn.metrics import precision_recall_curve
    from ml.s3_model import FRAME, TRAIN_END, rain_mask, rise_rate_fire, ts
    p = argparse.ArgumentParser()
    p.add_argument("--h", type=int, default=3)
    p.add_argument("--scores", default="ml/figures/s3_eval_scores.parquet")
    p.add_argument("--choices", default="ml/figures/s3_eval_choices.json")
    p.add_argument("--out", default="ml/figures/s3_pr_3h.png")
    a = p.parse_args(argv)
    h = a.h
    sc = pd.read_parquet(a.scores)
    sc = sc[sc.horizon_h == h]
    ch = json.load(open(a.choices))[str(h)]
    df = pd.read_parquet(FRAME)
    info = json.load(open("ml/figures/s3_crossings.json"))
    test = df[df.t >= ts(TRAIN_END)]
    test_rain = df[rain_mask(df) & (df.t >= ts(TRAIN_END))]

    def op(y, fire):
        tp = int((fire & (y == 1)).sum())
        return tp / max(int(fire.sum()), 1), tp / max(int(y.sum()), 1)

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                         "ytick.color": MUTED, "font.family": "DejaVu Sans"})
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.9), facecolor=SURFACE, sharey=True)
    panels = [("All test windows (38 gauges, 2024-2026)", test, [("s3_v0_all_test", "s3_v0", "v0 stage", "-")]),
              ("Rain-era test windows (event + dry hours)", test_rain,
               [("s3_v0_rain_test", "s3_v0", "v0 stage", "-"), ("s3_v1_rain_test", "s3_v1", "v1 stage + rain", "--")])]
    for ax, (title, rows, curves) in zip(axes, panels):
        ax.set_facecolor(SURFACE)
        y_rows = rows[f"y_cross_{h}"].to_numpy()
        for name, key, label, ls in curves:
            s = sc[sc.curve == name]
            pr, rc, th = precision_recall_curve(s.y, s.p)
            ax.plot(rc, pr, color=C[key], lw=2, ls=ls, solid_capstyle="round")
            tau = ch["v0_tau"] if key == "s3_v0" else ch["v1_tau"]
            pp, rr = op(s.y.to_numpy(), s.p.to_numpy() >= tau)
            ax.plot([rr], [pp], "o", ms=8, color=C[key], mec=SURFACE, mew=2, zorder=5)
            if key == "s3_v0":
                ax.annotate("v0 at its threshold", (rr, pp), xytext=(10, 6), textcoords="offset points",
                            color=INK, fontsize=8)
            else:                                            # crowded spot: label in open space with a leader line
                ax.annotate("v1 at its threshold", (rr, pp), xytext=(0.30, 0.26), textcoords="data", color=INK,
                            fontsize=8, arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.8})
        fire, _ = rise_rate_fire(rows, h, info)
        pp, rr = op(y_rows, fire)
        ax.plot([rr], [pp], "D", ms=8, color=C["rise_rate"], mec=SURFACE, mew=2, zorder=5)
        ax.annotate("rise-rate rule", (rr, pp), xytext=(8, -12), textcoords="offset points", color=INK, fontsize=8)
        base = y_rows.mean()
        ax.axhline(base, color=MUTED, lw=1, ls=":")
        ax.text(0.98, 0.96, f"dotted line: positive rate {base:.2%} (precision of a random rule)",
                transform=ax.transAxes, ha="right", va="top", color=MUTED, fontsize=7.5)
        ax.set_title(title, color=INK, fontsize=9.5, loc="left")
        ax.set_xlabel("recall (windows that cross minor within 3 h)")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 0.6)
        ax.grid(color=GRID, lw=0.8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    axes[0].set_ylabel("precision")
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], color=C["s3_v0"], lw=2, label="v0 stage only"),
               Line2D([], [], color=C["s3_v1"], lw=2, ls="--", label="v1 stage + rain (10 km)"),
               Line2D([], [], color=C["rise_rate"], marker="D", ls="", ms=7, label="rise-rate rule")]
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, fontsize=8.5, labelcolor=INK)
    fig.text(0.01, 0.015, f"Row-level precision-recall, {h} h horizon, test period; markers = operating points on the "
             "same rows. Event-level F1 and false alarms: ml/figures/s3_eval.csv.", fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    fig.savefig(a.out, dpi=200, facecolor=SURFACE)
    print(f"done -> {a.out}")


if __name__ == "__main__":
    main()

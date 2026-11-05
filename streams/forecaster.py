"""Forecaster (C1.4): aggregates.window -> forecasts.stage (+ alerts.flood when an official category is forecast).

    python -m streams.forecaster                 # run until Ctrl-C
    python -m streams.forecaster --group f2      # new consumer group = re-forecast every window on the topic

One forecast record per closed window per gauge (zero-filled windows too: the forecast is then the carried-forward
stage). The model is `ml.predict.predict()`; today that is the rise-rate baseline (no probabilities), from S3 the
trained model. Revisions of already-closed windows are skipped, so a forecast is made once per window.

Alerts (docs/CONTRACTS.md): two rules, and each alert records which one fired (`alert_rule`).
  alert_by_model  when the model gives it (p_minor >= tau while the stage is below minor, the rule its detection numbers
                  were measured with): a minor alert. The model says nothing about higher categories, so no upgrades.
  forecast_stage  otherwise (rise-rate record): the forecast reaches an official NWPS category above the one the gauge is in.
One *episode* per gauge and mode groups the alerts of one rise:
  new      first category forecast in the episode
  upgrade  a higher category is forecast later in the same episode
  retract  the forecast fell back below the lowest raised level for RETRACT_AFTER windows before the stage ever
           reached it (a withdrawn warning, never silently dropped). Under alert_by_model the forecast stands for its horizon,
           so it is withdrawn only after max(RETRACT_AFTER, horizon) windows without the model alerting
The episode closes after END_AFTER calm windows. Under alert_by_model it closes COOLDOWN_H after the alert fired, in data time,
as the evaluated protocol (ml/harness.py): no new alert before then, a new one possible from then on. Alerts are drafts: nothing is sent without approval.
Episode state is saved to state/forecaster_state.json before the offsets are committed, so a restart continues the
same episodes.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path

from confluent_kafka import Consumer

from common import config
from common.kafka_io import JsonProducer
from common.kafka_commit import commit_sync
from common.nwps import CATEGORIES, category_of
from common.timeutil import epoch_to_iso, now_epoch, to_epoch

log = logging.getLogger("forecaster")

HORIZON_H = float(os.getenv("FORECAST_HORIZON_H", "3"))
RETRACT_AFTER = int(os.getenv("FORECAST_RETRACT_AFTER", "2"))
END_AFTER = int(os.getenv("FORECAST_END_AFTER", "6"))
COOLDOWN_H = float(os.getenv("FORECAST_COOLDOWN_H", "6"))
HISTORY_WINDOWS = 24
GROUP = config.GROUP_FORECASTER
STATE_FILE = Path(config.STATE_DIR) / "forecaster_state.json"
GAUGES_CSV = Path(__file__).resolve().parent.parent / "config" / "gauge_locations.csv"


def load_gauges(path: Path = GAUGES_CSV) -> dict[str, dict]:
    """site_no -> {name, levels{action,minor,moderate,major}} from the official NWPS levels in the gauge table."""
    out: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            levels = {c: float(r[c]) for c in CATEGORIES if r.get(c) not in (None, "")}
            out[r["site_no"]] = {"name": r["name"], "levels": levels}
    return out


def rank(cat: str | None) -> int:
    return CATEGORIES.index(cat) if cat else -1


def new_state() -> dict:
    return {"history": [], "episode": None}


def forecast_record(agg: dict, pred: dict, now: float | None = None) -> dict:
    close = agg.get("window_close_time")
    return {
        "site_no": agg["site_no"], "mode": agg.get("mode", "LIVE"), "window_start": agg["window_start"],
        "window_start_ts": agg.get("window_start_ts"), "model": pred["model"], "horizon_h": pred["horizon_h"],
        "forecast_stage": pred.get("forecast_stage"), "p_action": pred.get("p_action"),
        "p_minor": pred.get("p_minor"), "p_moderate": pred.get("p_moderate"), "p_major": pred.get("p_major"),
        "made_at": epoch_to_iso(now if now is not None else now_epoch()),
        "trace": {"reading_ts": agg.get("last_reading_ts"), "fetched_ts": agg.get("fetched_ts"),
                  "ingest_ts": agg.get("ingest_ts"), "window_close_time": to_epoch(close) if isinstance(close, str) else close},
    }


def step(state: dict, agg: dict, gauge: dict, predict, horizon_h: float = HORIZON_H,
         now: float | None = None) -> tuple[dict, list[dict]]:
    """Advance one gauge by one closed window. Returns (forecast record, alert records); mutates `state`."""
    levels = gauge["levels"]
    features = {**agg, "history": list(state["history"]), "official_levels": levels, "horizon_h": horizon_h}
    pred = predict(features)
    fc = forecast_record(agg, pred, now)
    state["history"] = (state["history"] + [agg])[-HISTORY_WINDOWS:]

    cur, fstage = agg.get("stage_last"), pred.get("forecast_stage")
    alerts: list[dict] = []
    if cur is None or fstage is None:
        return fc, alerts
    by_model = pred.get("alert_by_model")
    rule = "forecast_stage" if by_model is None else "alert_by_model"
    cur_cat = category_of(cur, levels)
    fc_cat = category_of(fstage, levels) if by_model is None else ("minor" if by_model and "minor" in levels else None)
    ep = state["episode"]

    if ep:
        for a in ep["raised"].values():
            if cur >= levels[a["category"]]:
                a["reached"] = True
        lowest = min(levels[c] for c in (a["category"] for a in ep["raised"].values()))
        quiet = fstage < lowest if by_model is None else not by_model
        ep["calm"] = ep["calm"] + 1 if (quiet and cur < lowest) else 0
        if ep["calm"] >= (RETRACT_AFTER if by_model is None else max(RETRACT_AFTER, math.ceil(horizon_h))):
            for a in list(ep["raised"].values()):
                if not a["reached"] and not a.get("retracted"):
                    a["retracted"] = True
                    alerts.append(_alert(agg, gauge, pred, a["category"], a["alert_id"], "retract", ep["start"], cur, fc, rule))
        if by_model is None:
            done = ep["calm"] >= END_AFTER or all(a.get("retracted") for a in ep["raised"].values())
        else:
            fired = ep.get("fired_ts", to_epoch(ep["start"]))
            done = float(agg["window_start_ts"]) - fired >= COOLDOWN_H * 3600
        if done:
            state["episode"] = ep = None

    # a forecast of a higher category than the gauge is in now (and than any alert still standing) raises an alert
    if agg.get("n_readings", 0) > 0 and fc_cat and rank(fc_cat) > rank(cur_cat):
        standing = [rank(a["category"]) for a in (ep["raised"].values() if ep else []) if not a.get("retracted")]
        cooling = by_model is not None and ep is not None and not standing
        if rank(fc_cat) > max(standing, default=-1) and not cooling:
            kind = "upgrade" if ep and standing else "new"
            if ep is None:
                ep = state["episode"] = {"start": agg["window_start"], "raised": {}, "calm": 0, "fired_ts": float(agg["window_start_ts"])}
            alert_id = f"{agg['site_no']}|{fc_cat}|{ep['start']}"
            ep["raised"][fc_cat] = {"alert_id": alert_id, "category": fc_cat, "reached": cur >= levels[fc_cat]}
            alerts.append(_alert(agg, gauge, pred, fc_cat, alert_id, kind, ep["start"], cur, fc, rule))
    return fc, alerts


def _alert(agg, gauge, pred, category, alert_id, kind, episode_start, cur, fc, rule) -> dict:
    t = dict(fc["trace"])
    t["forecast_time"] = fc["made_at"]
    return {
        "alert_id": alert_id, "kind": kind, "site_no": agg["site_no"], "site_name": gauge["name"],
        "mode": agg.get("mode", "LIVE"), "category": category, "probability": pred.get(f"p_{category}"),
        "horizon_h": pred["horizon_h"], "forecast_stage": pred.get("forecast_stage"), "current_stage": cur,
        "official_levels": gauge["levels"], "provisional_data": bool(agg.get("provisional")),
        "model": pred["model"], "alert_rule": rule, "tau": pred.get("tau"), "raised_at": fc["made_at"], "window_start": agg["window_start"],
        "window_start_ts": agg.get("window_start_ts"), "episode_start": episode_start, "trace": t,
    }


# ---- state file -------------------------------------------------------------------------------------------------

def load_state(path: Path = STATE_FILE) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(states: dict[str, dict], path: Path = STATE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(states), encoding="utf-8")
    os.replace(tmp, path)


# ---- Kafka loop -------------------------------------------------------------------------------------------------

def run(group: str, exit_when_idle: float | None):
    from ml.predict import predict
    gauges = load_gauges()
    states = load_state() if group == GROUP else {}
    prod = JsonProducer("forecaster")
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": group, "enable.auto.commit": False,
                  "auto.offset.reset": "earliest", "isolation.level": "read_committed"})
    c.subscribe([config.TOPIC_AGG])
    n_fc = n_alert = n_skip = 0
    idle_since, last_log = time.monotonic(), time.monotonic()
    try:
        while True:
            msgs = c.consume(num_messages=200, timeout=1.0)
            got = 0
            for msg in msgs:
                if msg.error():
                    continue
                got += 1
                try:
                    agg = json.loads(msg.value())
                    site = agg["site_no"]
                    if agg.get("revision") or site not in gauges:
                        n_skip += 1
                        continue
                    key = f"{site}|{agg.get('mode', 'LIVE')}"
                    fc, alerts = step(states.setdefault(key, new_state()), agg, gauges[site], predict)
                except (KeyError, ValueError, TypeError) as e:
                    log.error("skipping bad window %s@%s: %r", msg.topic(), msg.offset(), e)
                    continue
                prod.send(config.TOPIC_FORECASTS, site, fc)
                n_fc += 1
                for a in alerts:
                    prod.send(config.TOPIC_ALERTS, site, a)
                    n_alert += 1
                    log.info("ALERT %s %s %s (%s) forecast %.2f ft, now %.2f ft", a["kind"], a["alert_id"], a["mode"],
                             a["model"], a["forecast_stage"], a["current_stage"])
            if got:
                prod.flush()
                if prod.failed:
                    raise RuntimeError(f"{prod.failed} messages failed to publish; offsets not committed")
                save_state(states)
                commit_sync(c)
                idle_since = time.monotonic()
            elif exit_when_idle is not None and time.monotonic() - idle_since > exit_when_idle:
                break
            if time.monotonic() - last_log > 10:
                log.info("forecasts %d, alerts %d, skipped %d", n_fc, n_alert, n_skip)
                last_log = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        prod.flush()
        c.close()
        log.info("forecaster stopped: forecasts %d, alerts %d, skipped %d", n_fc, n_alert, n_skip)
    return n_fc, n_alert


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", default=GROUP)
    ap.add_argument("--exit-when-idle", type=float)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("forecaster starting: group %s, horizon %g h", a.group, HORIZON_H)
    run(a.group, a.exit_when_idle)


if __name__ == "__main__":
    main()

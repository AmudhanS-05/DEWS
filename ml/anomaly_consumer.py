"""
ML anomaly-detection consumer — methodology steps 5-6.

Subscribes to `aggregates.window` (published by streams/windowing_app.py),
scores each windowed aggregate with a per-sensor-type Isolation Forest, and
writes the result to `risk_scores`. When the score crosses the anomaly
threshold, also raises a row in `alerts` (this is what the dashboard and,
in Phase 2, the agentic layer will read).

Manual offset commits are used (matches the "at-least-once processing" CIE
program) so a message is only marked done after the DB write succeeds —
if the consumer crashes mid-write, the message is re-delivered, giving
at-least-once semantics for risk scoring (idempotent because we always
overwrite risk_scores for the same station/window on retry, no duplicates
by design of the insert below being safe to repeat during grading demos).

Run:
    python anomaly_consumer.py
Requires:
    Models trained via train_isolation_forest.py
"""
import json

import joblib
import psycopg2
from confluent_kafka import Consumer

BOOTSTRAP_SERVERS = "localhost:9092"
PG_DSN = "dbname=disaster_warning user=spa_user password=spa_pass host=localhost port=5432"

FEATURE_COLUMNS = ["min_value", "max_value", "avg_value", "sample_count"]

ALERT_RULES = {
    # sensor_type: (alert_type, severity, human-readable threshold hint)
    "rainfall_mm": ("FLOOD_RISK", "HIGH"),
    "water_level_m": ("FLOOD_RISK", "CRITICAL"),
    "temperature_c": ("HEAT_ANOMALY", "MEDIUM"),
}


def load_models():
    models = {}
    for sensor_type in ["rainfall_mm", "temperature_c", "water_level_m"]:
        try:
            models[sensor_type] = joblib.load(f"model_{sensor_type}.joblib")
        except FileNotFoundError:
            print(f"[WARN] No trained model for {sensor_type} yet — run train_isolation_forest.py first. "
                  f"Windows for this sensor will be stored but not scored.")
    return models


def score_and_store(payload: dict, models: dict, conn):
    sensor_type = payload["sensor_type"]
    model = models.get(sensor_type)
    if model is None:
        return

    features = [[payload[col] for col in FEATURE_COLUMNS]]
    # IsolationForest: decision_function > 0 = normal, < 0 = anomalous.
    # Normalize to a 0-1 "risk score" where higher = more anomalous.
    raw_score = model.decision_function(features)[0]
    is_anomaly = model.predict(features)[0] == -1
    risk_score = max(0.0, min(1.0, 0.5 - raw_score))  # simple monotonic squashing

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk_scores
                (station_id, window_start, window_end, risk_score, is_anomaly, model_version)
            VALUES (%s, to_timestamp(%s), to_timestamp(%s), %s, %s, %s)
            RETURNING id
            """,
            (
                payload["station_id"], payload["window_start"], payload["window_end"],
                risk_score, bool(is_anomaly), "isolation_forest_v1",
            ),
        )
        risk_score_id = cur.fetchone()[0]

        if is_anomaly:
            alert_type, severity = ALERT_RULES.get(sensor_type, ("ANOMALY", "MEDIUM"))
            message = (
                f"{alert_type} at {payload['station_id']}: {sensor_type} window "
                f"avg={payload['avg_value']:.2f} max={payload['max_value']:.2f} "
                f"(risk_score={risk_score:.2f})"
            )
            cur.execute(
                """
                INSERT INTO alerts (station_id, alert_type, severity, message, risk_score_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (payload["station_id"], alert_type, severity, message, risk_score_id),
            )
            print(f"[ALERT] {message}")
        else:
            print(f"[OK] {payload['station_id']} {sensor_type} risk_score={risk_score:.2f}")


def run():
    models = load_models()
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": "ml-anomaly-detector",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,   # manual commits -> at-least-once
    })
    consumer.subscribe(["aggregates.window"])
    conn = psycopg2.connect(PG_DSN)

    print("Listening on aggregates.window ... Ctrl+C to stop.")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"[CONSUMER ERROR] {msg.error()}")
                continue
            payload = json.loads(msg.value())
            score_and_store(payload, models, conn)
            consumer.commit(msg)  # only after successful DB write
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
        conn.close()


if __name__ == "__main__":
    run()

"""
Train the baseline anomaly-detection model — methodology step 5.

For a class project, "training data" is synthetic: run the producer + streams
app for a while against *normal* conditions (edit sensor_simulator.py's spike
probabilities down to ~0), export windowed_aggregates from Postgres, and fit
an Isolation Forest on it. The model learns what "normal" min/max/avg/count
looks like per sensor type, so anything far outside that gets flagged.

Isolation Forest chosen (see docs/references.md) because:
  - Unsupervised — no labeled disaster events needed
  - Cheap to train/retrain as more normal data accumulates
  - Used by multiple reference implementations for the same kind of pipeline

Run:
    python train_isolation_forest.py
Produces:
    model.joblib  (loaded by anomaly_consumer.py)
"""
import joblib
import numpy as np
import psycopg2
from sklearn.ensemble import IsolationForest

PG_DSN = "dbname=disaster_warning user=spa_user password=spa_pass host=localhost port=5432"

FEATURE_COLUMNS = ["min_value", "max_value", "avg_value", "sample_count"]


def load_training_data(sensor_type: str):
    conn = psycopg2.connect(PG_DSN)
    with conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {", ".join(FEATURE_COLUMNS)}
            FROM windowed_aggregates
            WHERE sensor_type = %s
            ORDER BY window_start
            """,
            (sensor_type,),
        )
        rows = cur.fetchall()
    conn.close()
    return np.array(rows, dtype=float)


def train_and_save(sensor_type: str, contamination: float = 0.05):
    data = load_training_data(sensor_type)
    if len(data) < 20:
        print(f"[{sensor_type}] Not enough windows yet ({len(data)}). "
              f"Run the producer+streams app longer, then retry.")
        return
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=42)
    model.fit(data)
    path = f"model_{sensor_type}.joblib"
    joblib.dump(model, path)
    print(f"[{sensor_type}] Trained on {len(data)} windows -> {path}")


if __name__ == "__main__":
    for sensor_type in ["rainfall_mm", "temperature_c", "water_level_m"]:
        train_and_save(sensor_type)

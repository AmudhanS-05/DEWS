"""
Sensor simulator + Kafka producer.

Covers methodology steps 1-3:
  1. Streaming variables: rainfall, temperature, wind_speed, humidity, water_level
  2. Publishes to two topics by category (weather vs water-level), JSON-serialized
  3. Keys by station_id so partitioning/consumer-groups can be demonstrated
     (same station always lands on the same partition -> ordered per station)

Delivery is confirmed asynchronously via a callback (matches the "asynchronous
send confirmation" CIE program), and acks/retries are set for reliability
(matches the "payment system reliability" CIE program pattern, reused here for
sensor data instead of financial transactions).

Run:
    python sensor_simulator.py
Requires:
    docker compose up -d   (from ../docker)
    topics created (see ../docker/README.md)
"""
import json
import random
import time
from datetime import datetime, timezone

from confluent_kafka import Producer

BOOTSTRAP_SERVERS = "localhost:9092"

STATIONS = ["STATION_A", "STATION_B", "STATION_C"]

WEATHER_TOPIC = "sensors.weather"
WATER_TOPIC = "sensors.water-level"

producer_conf = {
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "acks": "all",          # wait for all in-sync replicas
    "retries": 5,
    "linger.ms": 50,        # small batching window
    "enable.idempotence": True,
}
producer = Producer(producer_conf)


def delivery_callback(err, msg):
    if err is not None:
        print(f"[DELIVERY FAILED] {msg.key()}: {err}")
    else:
        print(f"[DELIVERED] {msg.topic()} partition={msg.partition()} offset={msg.offset()}")


def make_weather_reading(station_id: str) -> dict:
    return {
        "station_id": station_id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "rainfall_mm": round(random.uniform(0, 5) + random.choice([0, 0, 0, 40]), 2),  # occasional spike
        "temperature_c": round(random.uniform(18, 40) + random.choice([0, 0, 0, 15]), 2),
        "wind_speed_kmh": round(random.uniform(0, 30) + random.choice([0, 0, 0, 80]), 2),
        "humidity_pct": round(random.uniform(30, 90), 2),
    }


def make_water_level_reading(station_id: str) -> dict:
    return {
        "station_id": station_id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "water_level_m": round(random.uniform(0.5, 2.0) + random.choice([0, 0, 0, 3.5]), 2),
    }


def run(interval_seconds: float = 1.0):
    print(f"Producing to {BOOTSTRAP_SERVERS} ... Ctrl+C to stop.")
    try:
        while True:
            for station in STATIONS:
                weather = make_weather_reading(station)
                producer.produce(
                    WEATHER_TOPIC,
                    key=station,
                    value=json.dumps(weather).encode("utf-8"),
                    callback=delivery_callback,
                )
                water = make_water_level_reading(station)
                producer.produce(
                    WATER_TOPIC,
                    key=station,
                    value=json.dumps(water).encode("utf-8"),
                    callback=delivery_callback,
                )
            producer.poll(0)  # trigger delivery callbacks
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        print("Flushing remaining messages...")
        producer.flush(10)


if __name__ == "__main__":
    run()

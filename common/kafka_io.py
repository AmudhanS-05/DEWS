"""Kafka producer with the reliability settings we claim in the report:
acks=all + idempotence (no duplicates from producer retries), bounded linger for
small batches. Delivery failures are appended to state/failed_publish.jsonl so
they are never silently lost."""
from __future__ import annotations

import json
import os

from confluent_kafka import Producer

from common import config


class JsonProducer:
    def __init__(self, client_id: str, bootstrap: str | None = None):
        self.p = Producer({
            "bootstrap.servers": bootstrap or config.KAFKA_BOOTSTRAP,
            "client.id": client_id,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 50,
            "compression.type": "lz4",
        })
        self.sent = 0
        self.failed = 0
        os.makedirs(config.STATE_DIR, exist_ok=True)
        self.fail_path = os.path.join(config.STATE_DIR, "failed_publish.jsonl")

    def _cb(self, err, msg):
        if err is None:
            self.sent += 1
            return
        self.failed += 1
        with open(self.fail_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"topic": msg.topic(), "key": (msg.key() or b"").decode(),
                                "value": (msg.value() or b"").decode(), "error": str(err)}) + "\n")

    def send(self, topic: str, key: str, value: dict):
        payload = json.dumps(value).encode()
        while True:
            try:
                self.p.produce(topic, key=key.encode(), value=payload, on_delivery=self._cb)
                break
            except BufferError:          # local queue full (fast replay): let deliveries drain
                self.p.poll(0.5)
        self.p.poll(0)

    def flush(self, timeout: float = 30.0) -> int:
        return self.p.flush(timeout)

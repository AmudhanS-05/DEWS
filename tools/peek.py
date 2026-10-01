"""Print messages from a topic (for checking each phase by eye).

    python -m tools.peek gauge.usgs --n 5
    python -m tools.peek aggregates.window --n 3 --from-beginning
"""
from __future__ import annotations

import argparse
import json
import uuid

from confluent_kafka import Consumer

from common import config


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("topic")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--from-beginning", action="store_true")
    p.add_argument("--timeout-s", type=float, default=30)
    a = p.parse_args(argv)
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP,
                  "group.id": f"peek-{uuid.uuid4().hex[:8]}",     # throwaway group: never moves real offsets
                  "auto.offset.reset": "earliest" if a.from_beginning else "latest",
                  "enable.auto.commit": False})
    c.subscribe([a.topic])
    got = 0
    try:
        while got < a.n:
            m = c.poll(a.timeout_s)
            if m is None:
                print(f"(no message within {a.timeout_s}s)")
                break
            if m.error():
                print("error:", m.error())
                continue
            got += 1
            print(f"--- {a.topic} p{m.partition()} @{m.offset()} key={m.key().decode() if m.key() else None}")
            print(json.dumps(json.loads(m.value()), indent=1))
    finally:
        c.close()


if __name__ == "__main__":
    main()

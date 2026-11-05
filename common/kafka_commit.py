"""Offset commits that survive a broker failover."""
from __future__ import annotations


def commit_sync(consumer, retry_s: float = 60.0, **kw) -> bool:
    """Synchronous offset commit that survives a broker failover: while the group coordinator moves to another broker
    (a broker stopped), the commit fails with a retriable error for a few seconds. Retry instead of crashing the consumer.
    If it still fails, give up on this commit only: the records are redelivered and every writer here is idempotent."""
    import logging
    import time

    try:
        from confluent_kafka import KafkaException
    except ImportError:          # a stub module in tests
        KafkaException = Exception
    t0 = time.monotonic()
    while True:
        try:
            consumer.commit(asynchronous=False, **kw)
            return True
        except KafkaException as e:
            err = e.args[0] if e.args else None
            fatal = getattr(err, "fatal", lambda: False)()
            if fatal or time.monotonic() - t0 > retry_s:
                logging.getLogger("kafka_io").warning("offset commit failed, records will be redelivered: %s", err)
                return False
            time.sleep(1)

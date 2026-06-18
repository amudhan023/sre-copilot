"""Kafka producer/consumer wrappers with retry and replay support."""
from __future__ import annotations
import json
import logging
import os
import time
import uuid
from typing import Callable

from confluent_kafka import Consumer, Producer, KafkaException, KafkaError, TopicPartition

logger = logging.getLogger(__name__)

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")


def _wait_for_kafka(max_retries: int = 30, delay: float = 5.0) -> None:
    from confluent_kafka.admin import AdminClient
    for attempt in range(1, max_retries + 1):
        try:
            admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
            meta = admin.list_topics(timeout=5)
            if meta:
                logger.info("Kafka is ready.")
                return
        except Exception as exc:
            logger.warning("Kafka not ready (attempt %d/%d): %s", attempt, max_retries, exc)
            time.sleep(delay)
    raise RuntimeError("Kafka never became available.")


def make_producer() -> Producer:
    _wait_for_kafka()
    return Producer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "acks": "all",
        "retries": 5,
        "retry.backoff.ms": 1000,
        "linger.ms": 5,
    })


def publish(producer: Producer, topic: str, message: dict, key: str | None = None) -> None:
    def _cb(err, msg):
        if err:
            logger.error("Delivery failed for topic %s: %s", topic, err)

    key_bytes = key.encode("utf-8") if key else None
    producer.produce(
        topic,
        json.dumps(message).encode("utf-8"),
        key=key_bytes,
        callback=_cb,
    )
    producer.poll(0)


def flush(producer: Producer) -> None:
    producer.flush(timeout=10)


def make_consumer(
    topics: list[str],
    group_id: str,
    auto_offset_reset: str = "latest",
    max_retries: int = 30,
) -> Consumer:
    _wait_for_kafka(max_retries=max_retries)
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "auto.offset.reset": auto_offset_reset,
        "enable.auto.commit": True,
        "session.timeout.ms": 30000,
        "heartbeat.interval.ms": 10000,
    })
    consumer.subscribe(topics)
    logger.info("Subscribed to topics: %s (group: %s)", topics, group_id)
    return consumer


def consume_loop(
    consumer: Consumer,
    handler: Callable[[dict], None],
    poll_timeout: float = 1.0,
) -> None:
    """Blocking consume loop — calls handler for each valid message."""
    try:
        while True:
            msg = consumer.poll(timeout=poll_timeout)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Consumer error: %s", msg.error())
                continue
            try:
                value = json.loads(msg.value().decode("utf-8"))
                handler(value)
            except Exception as exc:
                logger.exception("Error handling message from %s: %s", msg.topic(), exc)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()


def replay_window(
    topic: str,
    since_ms: int,
    until_ms: int | None = None,
    service_filter: str | None = None,
    max_messages: int = 5000,
) -> list[dict]:
    """
    Replay messages from a topic within a time window.

    Uses a temporary consumer group so it doesn't affect existing consumers.
    Seeks all partitions to the given timestamp offset and reads forward.
    Returns filtered messages as a list of dicts.
    """
    _wait_for_kafka()
    group_id = f"replay-{uuid.uuid4().hex[:8]}"
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })

    try:
        meta = consumer.list_topics(topic, timeout=10)
        if topic not in meta.topics:
            logger.warning("Topic %s not found for replay.", topic)
            return []

        partitions = [
            TopicPartition(topic, p)
            for p in meta.topics[topic].partitions
        ]

        # Find offsets for since_ms across all partitions
        ts_partitions = [TopicPartition(topic, p.partition, since_ms) for p in partitions]
        offsets = consumer.offsets_for_times(ts_partitions, timeout=10)

        # Assign and seek to found offsets; skip partitions with no data at that time
        valid = [tp for tp in offsets if tp.offset >= 0]
        if not valid:
            return []

        consumer.assign(valid)
        for tp in valid:
            consumer.seek(tp)

        messages: list[dict] = []
        end_time = until_ms or (since_ms + 3600_000)  # default: 1 hour window

        while len(messages) < max_messages:
            msg = consumer.poll(timeout=2.0)
            if msg is None:
                break
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    break
                continue
            if msg.timestamp()[1] > end_time:
                break
            try:
                value = json.loads(msg.value().decode("utf-8"))
                if service_filter is None or value.get("source_service") == service_filter:
                    messages.append(value)
            except Exception:
                pass

        return messages
    finally:
        consumer.close()

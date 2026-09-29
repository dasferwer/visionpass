import json
from collections.abc import Mapping
from typing import Any

import pika

from visionpass.config import get_settings

EXCHANGE = "visionpass.events"
ENROLLMENT_QUEUE = "visionpass.enrollments"
INVALID_QUEUE = "visionpass.enrollments.invalid"


def connect() -> pika.BlockingConnection:
    parameters = pika.URLParameters(get_settings().rabbitmq_url)
    parameters.connection_attempts = 3
    parameters.retry_delay = 1
    parameters.socket_timeout = 5
    return pika.BlockingConnection(parameters)


def declare_topology(channel: pika.channel.Channel) -> None:
    channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    channel.queue_declare(queue=INVALID_QUEUE, durable=True)
    channel.queue_declare(queue=ENROLLMENT_QUEUE, durable=True)
    channel.queue_bind(
        queue=ENROLLMENT_QUEUE,
        exchange=EXCHANGE,
        routing_key="biometric.enrollment.requested",
    )


def publish_event(channel: pika.channel.Channel, event_type: str, body: Mapping[str, Any]) -> None:
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key=event_type,
        mandatory=True,
        body=json.dumps(body).encode(),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=str(body["event_id"]),
            type=event_type,
        ),
    )


def check_connection() -> None:
    connection = connect()
    connection.close()

import json
import logging
import time
from uuid import UUID

import pika

from visionpass.broker import ENROLLMENT_QUEUE, INVALID_QUEUE, connect, declare_topology
from visionpass.cv import get_encoder
from visionpass.db import SessionLocal
from visionpass.models import OutboxEvent
from visionpass.services import process_next_enrollment

logger = logging.getLogger(__name__)


def consume_one(channel: pika.channel.Channel) -> bool:
    method, _properties, body = channel.basic_get(ENROLLMENT_QUEUE, auto_ack=False)
    if method is None:
        return False
    try:
        payload = json.loads(body)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("event_id"), str)
            or not isinstance(payload.get("template_id"), str)
            or not isinstance(payload.get("generation"), int)
            or isinstance(payload.get("generation"), bool)
        ):
            raise ValueError("Некорректная структура сообщения")
        event_id, template_id = UUID(payload["event_id"]), UUID(payload["template_id"])
        with SessionLocal() as db:
            event = db.get(OutboxEvent, event_id)
            if (
                event is None
                or event.event_type != "biometric.enrollment.requested"
                or event.payload.get("template_id") != str(template_id)
                or event.payload.get("generation") != payload["generation"]
            ):
                raise ValueError("Сообщение не соответствует событию outbox")
    except (ValueError, TypeError, KeyError):
        channel.basic_publish(
            exchange="",
            routing_key=INVALID_QUEUE,
            body=body,
            properties=pika.BasicProperties(delivery_mode=2),
            mandatory=True,
        )
    channel.basic_ack(delivery_tag=method.delivery_tag)
    return True


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("pika").setLevel(logging.WARNING)
    while True:
        connection = None
        try:
            with SessionLocal() as db:
                process_next_enrollment(db, get_encoder())
            connection = connect()
            channel = connection.channel()
            declare_topology(channel)
            channel.confirm_delivery()
            while connection.is_open:
                consumed = consume_one(channel)
                with SessionLocal() as db:
                    worked = process_next_enrollment(db, get_encoder())
                connection.process_data_events(time_limit=0 if consumed or worked else 1)
        except Exception as exc:
            logger.warning("enrollment_retry kind=%s", type(exc).__name__)
            time.sleep(3)
        finally:
            if connection is not None and connection.is_open:
                connection.close()


if __name__ == "__main__":
    run()

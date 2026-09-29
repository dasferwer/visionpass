"""Удалять биометрию после истечения срока хранения."""

import logging
import time

from visionpass.db import SessionLocal
from visionpass.services import expire_sensitive_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run() -> None:
    while True:
        try:
            with SessionLocal() as db:
                cleaned = expire_sensitive_data(db)
            if cleaned:
                logger.info("biometric_expired count=%s", cleaned)
        except Exception as exc:
            logger.warning("privacy_retry kind=%s", type(exc).__name__)
        time.sleep(10)


if __name__ == "__main__":
    run()

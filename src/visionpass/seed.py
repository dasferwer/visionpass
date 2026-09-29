import logging

from sqlalchemy import select

from visionpass.config import get_settings
from visionpass.db import SessionLocal
from visionpass.models import User, UserRole
from visionpass.security import hash_password

logger = logging.getLogger(__name__)


def ensure_user(email: str, name: str, password: str, role: UserRole) -> None:
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.email == email.lower())) is None:
            db.add(
                User(
                    email=email.lower(),
                    full_name=name,
                    password_hash=hash_password(password),
                    role=role,
                )
            )


def seed_database() -> None:
    settings = get_settings()
    ensure_user(
        str(settings.admin_email),
        "Администратор VisionPass",
        settings.admin_password.get_secret_value(),
        UserRole.ADMIN,
    )
    ensure_user(
        str(settings.reviewer_email),
        "Проверяющий VisionPass",
        settings.reviewer_password.get_secret_value(),
        UserRole.REVIEWER,
    )
    logger.info("Демонстрационные учётные записи подготовлены")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_database()

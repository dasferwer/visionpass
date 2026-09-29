"""Проверить переход исторической биометрии на шифрование во временной базе."""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, text

from visionpass.crypto import decrypt
from visionpass.db import engine


def main() -> None:
    if engine.url.database != "visionpass_test":
        raise RuntimeError("Проверка разрешена только из БД visionpass_test")
    temporary = "migration_" + uuid4().hex
    admin = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target = create_engine(engine.url.set(database=temporary))
    environment = {
        **os.environ,
        "VISIONPASS_DATABASE_URL": target.url.render_as_string(hide_password=False),
    }
    event_id, participant_id, template_id, attempt_id = [uuid4() for _ in range(4)]
    now = datetime.now(UTC)
    try:
        with admin.connect() as connection:
            connection.execute(text("CREATE DATABASE " + temporary))
        subprocess.run(["alembic", "upgrade", "20260831_0001"], env=environment, check=True)
        with target.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO events (id,name,starts_at,ends_at) "
                    "VALUES (:id,'Историческое событие',:start,:finish)"
                ),
                {"id": event_id, "start": now, "finish": now + timedelta(days=1)},
            )
            connection.execute(
                text(
                    "INSERT INTO participants (id,event_id,external_id,full_name,email) "
                    "VALUES (:id,:event,'old-1','Участник','old@example.com')"
                ),
                {"id": participant_id, "event": event_id},
            )
            connection.execute(
                text(
                    "INSERT INTO biometric_templates "
                    "(id,participant_id,status,embedding,pending_image,consent_at) "
                    "VALUES (:id,:participant,'pending',CAST(:embedding AS jsonb),:image,:consent)"
                ),
                {
                    "id": template_id,
                    "participant": participant_id,
                    "embedding": json.dumps([0.1, 0.2]),
                    "image": b"historic-photo",
                    "consent": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO access_attempts (id,event_id,participant_id,decision,reason) "
                    "VALUES (:id,:event,:participant,'review','Требуется проверка')"
                ),
                {"id": attempt_id, "event": event_id, "participant": participant_id},
            )
        subprocess.run(["alembic", "upgrade", "head"], env=environment, check=True)
        with target.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT generation, encoder_id, encrypted_embedding, "
                        "encrypted_image, embedding, pending_image "
                        "FROM biometric_templates WHERE id=:id"
                    ),
                    {"id": template_id},
                )
                .mappings()
                .one()
            )
            assert row["embedding"] is None and row["pending_image"] is None
            assert row["encoder_id"] == "legacy-unverified"
            assert json.loads(decrypt(row["encrypted_embedding"], template_id, 1, "embedding")) == [
                0.1,
                0.2,
            ]
            assert decrypt(row["encrypted_image"], template_id, 1, "image") == b"historic-photo"
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM access_attempts WHERE id=:id"), {"id": attempt_id}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT full_name FROM participants WHERE id=:id"), {"id": participant_id}
                )
                == "Участник"
            )
        print("Исторические данные сохранены и зашифрованы")
    finally:
        target.dispose()
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE IF EXISTS " + temporary))
        admin.dispose()


if __name__ == "__main__":
    main()

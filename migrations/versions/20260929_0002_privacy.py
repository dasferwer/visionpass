"""Зашифровать биометрию и добавить поколения, сроки хранения и аренду обработки."""

import json
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

from visionpass.crypto import encrypt

revision = "20260929_0002"
down_revision = "20260831_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in [
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("encoder_id", sa.String(100)),
        sa.Column("encrypted_embedding", sa.LargeBinary()),
        sa.Column("encrypted_image", sa.LargeBinary()),
        sa.Column("image_expires_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    ]:
        op.add_column("biometric_templates", column)
    op.add_column("access_attempts", sa.Column("template_generation", sa.Integer()))
    op.add_column("access_attempts", sa.Column("encoder_id", sa.String(100)))
    connection = op.get_bind()
    cursor = None
    while True:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT id, embedding, pending_image FROM biometric_templates "
                    "WHERE (CAST(:cursor AS uuid) IS NULL OR id > CAST(:cursor AS uuid)) "
                    "ORDER BY id LIMIT 100"
                ),
                {"cursor": str(cursor) if cursor else None},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            vector = (
                encrypt(json.dumps(row["embedding"]).encode(), row["id"], 1, "embedding")
                if row["embedding"] is not None
                else None
            )
            image = (
                encrypt(bytes(row["pending_image"]), row["id"], 1, "image")
                if row["pending_image"] is not None
                else None
            )
            connection.execute(
                sa.text(
                    "UPDATE biometric_templates SET encrypted_embedding=:vector, "
                    "encrypted_image=:image, embedding=NULL, pending_image=NULL, "
                    "encoder_id='legacy-unverified', "
                    "image_expires_at=:image_expiry, expires_at=:expiry WHERE id=:id"
                ),
                {
                    "vector": vector,
                    "image": image,
                    "image_expiry": datetime.now(UTC) + timedelta(minutes=15),
                    "expiry": datetime.now(UTC) + timedelta(days=1),
                    "id": row["id"],
                },
            )
        cursor = rows[-1]["id"]
    op.create_check_constraint(
        "ck_no_plain_biometrics",
        "biometric_templates",
        "embedding IS NULL AND pending_image IS NULL",
    )
    op.create_check_constraint("ck_template_generation", "biometric_templates", "generation > 0")
    op.create_index("ix_template_image_expiry", "biometric_templates", ["image_expires_at"])
    op.create_index("ix_template_expiry", "biometric_templates", ["expires_at"])


def downgrade() -> None:
    raise RuntimeError(
        "Откат вернёт открытые биометрические данные; используйте исправляющую миграцию"
    )

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from visionpass.cv import DeterministicDemoEncoder
from visionpass.db import SessionLocal
from visionpass.models import BiometricTemplate, Participant, TemplateStatus
from visionpass.services import process_enrollment


def test_enrollment_access_and_biometric_deletion(
    client: TestClient,
    admin_headers: dict[str, str],
    event_and_participant: tuple[dict[str, object], dict[str, object]],
) -> None:
    event, participant = event_and_participant
    image = b"same-demo-face-bytes"
    enrollment = client.post(
        f"/api/v1/participants/{participant['id']}/enrollment",
        headers=admin_headers,
        data={"consent": "true"},
        files={"photo": ("face.jpg", image, "image/jpeg")},
    )
    assert enrollment.status_code == 202
    template_id = UUID(enrollment.json()["id"])

    with SessionLocal() as db:
        processed = process_enrollment(db, template_id, DeterministicDemoEncoder())
    assert processed.status == TemplateStatus.READY
    assert processed.pending_image is None
    assert processed.embedding is not None

    granted = client.post(
        "/api/v1/access/check",
        headers=admin_headers,
        data={"event_id": str(event["id"])},
        files={"photo": ("probe.jpg", image, "image/jpeg")},
    )
    denied = client.post(
        "/api/v1/access/check",
        headers=admin_headers,
        data={"event_id": str(event["id"])},
        files={"photo": ("other.jpg", b"different-face", "image/jpeg")},
    )
    assert granted.status_code == 201
    assert granted.json()["decision"] == "granted"
    assert granted.json()["participant_id"] == participant["id"]
    assert denied.status_code == 201
    assert denied.json()["decision"] == "denied"

    deleted = client.delete(
        f"/api/v1/participants/{participant['id']}/biometric",
        headers=admin_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    with SessionLocal() as db:
        stored = db.scalar(select(BiometricTemplate).where(BiometricTemplate.id == template_id))
        assert stored is not None
        assert stored.embedding is None
        assert stored.pending_image is None
        assert (
            db.scalar(
                text("SELECT embedding IS NULL FROM biometric_templates WHERE id = :id"),
                {"id": template_id},
            )
            is True
        )


def test_enrollment_requires_explicit_consent(
    client: TestClient,
    admin_headers: dict[str, str],
    event_and_participant: tuple[dict[str, object], dict[str, object]],
) -> None:
    _, participant = event_and_participant
    response = client.post(
        f"/api/v1/participants/{participant['id']}/enrollment",
        headers=admin_headers,
        data={"consent": "false"},
        files={"photo": ("face.jpg", b"face", "image/jpeg")},
    )
    assert response.status_code == 422


def test_demo_data_deletion_anonymizes_participant(
    client: TestClient,
    admin_headers: dict[str, str],
    event_and_participant: tuple[dict[str, object], dict[str, object]],
) -> None:
    _, participant = event_and_participant
    response = client.delete(
        f"/api/v1/participants/{participant['id']}/demo-data",
        headers=admin_headers,
    )
    assert response.status_code == 204
    with SessionLocal() as db:
        stored = db.get(Participant, UUID(str(participant["id"])))
        assert stored is not None
        assert stored.deleted_at is not None
        assert stored.full_name == "Deleted participant"
        assert stored.email.endswith("@example.invalid")

from uuid import UUID

from fastapi.testclient import TestClient

from visionpass.db import SessionLocal
from visionpass.models import AccessAttempt, AccessDecision


def test_uncertain_attempt_can_be_manually_reviewed_once(
    client: TestClient,
    reviewer_headers: dict[str, str],
    event_and_participant: tuple[dict[str, object], dict[str, object]],
) -> None:
    event, participant = event_and_participant
    with SessionLocal.begin() as db:
        attempt = AccessAttempt(
            event_id=UUID(str(event["id"])),
            participant_id=UUID(str(participant["id"])),
            decision=AccessDecision.REVIEW,
            distance=0.65,
            confidence=0.35,
            reason="Uncertain match requires manual review",
        )
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id

    first = client.post(
        f"/api/v1/access/attempts/{attempt_id}/review",
        headers=reviewer_headers,
        json={"resolution": "granted", "note": "Identity document checked at the gate."},
    )
    replay = client.post(
        f"/api/v1/access/attempts/{attempt_id}/review",
        headers=reviewer_headers,
        json={"resolution": "denied", "note": "A duplicate decision must be rejected."},
    )
    assert first.status_code == 201
    assert first.json()["resolution"] == "granted"
    assert replay.status_code == 409


def test_audit_log_never_contains_raw_image(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/audit", headers=admin_headers)

    assert response.status_code == 200
    assert "pending_image" not in response.text
    assert "same-demo-face-bytes" not in response.text

# ruff: noqa: E402
import pytest

from visionpass.test_safety import UnsafeTestEnvironment, ensure_test_environment

# Проверяем окружение раньше settings/engine и регистрации любых fixtures.
try:
    ensure_test_environment()
except UnsafeTestEnvironment as exc:
    raise pytest.UsageError(str(exc)) from None

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import text

from visionpass.config import get_settings
from visionpass.db import engine
from visionpass.main import app
from visionpass.seed import seed_database


@pytest.fixture(scope="session", autouse=True)
def reset_database() -> Generator[None, None, None]:
    if engine.url.database != "visionpass_test":
        raise RuntimeError("Тесты разрешены только в visionpass_test")
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE outbox_events, audit_logs, manual_reviews, access_attempts, "
                "biometric_templates, participants, events, users CASCADE"
            )
        )
    seed_database()
    yield


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    return login(client, str(settings.admin_email), settings.admin_password.get_secret_value())


@pytest.fixture(scope="session")
def reviewer_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    return login(
        client,
        str(settings.reviewer_email),
        settings.reviewer_password.get_secret_value(),
    )


@pytest.fixture
def event_and_participant(
    client: TestClient, admin_headers: dict[str, str]
) -> tuple[dict[str, object], dict[str, object]]:
    event_response = client.post(
        "/api/v1/events",
        headers=admin_headers,
        json={
            "name": "Backend Conference",
            "starts_at": "2030-09-01T09:00:00Z",
            "ends_at": "2030-09-01T18:00:00Z",
        },
    )
    assert event_response.status_code == 201
    event = event_response.json()
    participant_response = client.post(
        f"/api/v1/events/{event['id']}/participants",
        headers=admin_headers,
        json={
            "external_id": f"guest-{event['id']}",
            "full_name": "Test Participant",
            "email": f"guest-{event['id']}@example.com",
        },
    )
    assert participant_response.status_code == 201
    return event, participant_response.json()

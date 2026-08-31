from fastapi.testclient import TestClient


def test_health_reports_demo_backend(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "rabbitmq": "ok",
        "matcher_backend": "stub",
    }


def test_protected_endpoint_requires_login(client: TestClient) -> None:
    assert client.get("/api/v1/users/me").status_code == 401

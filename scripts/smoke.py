"""Проверить полный цикл демо: регистрация, обработка, доступ и удаление."""

import json
import os
import time
import urllib.request
from uuid import uuid4

base = os.environ.get("VISIONPASS_SMOKE_URL", "http://localhost:8000")


def call(
    path: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
    method: str = "POST",
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response) if response.status != 204 else {}


def multipart(path: str, fields: dict[str, str], token: str) -> dict[str, object]:
    boundary = uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
            'filename="demo.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
        ).encode()
        + b"demo-face"
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        base + path,
        data=b"".join(parts),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> None:
    token = str(
        call(
            "/api/v1/auth/login",
            {
                "email": os.environ["VISIONPASS_ADMIN_EMAIL"],
                "password": os.environ["VISIONPASS_ADMIN_PASSWORD"],
            },
        )["access_token"]
    )
    event = call(
        "/api/v1/events",
        {
            "name": "Проверка VisionPass",
            "starts_at": "2030-01-01T09:00:00Z",
            "ends_at": "2030-01-01T18:00:00Z",
        },
        token,
    )
    participant = call(
        f"/api/v1/events/{event['id']}/participants",
        {
            "external_id": uuid4().hex,
            "full_name": "Тестовый участник",
            "email": f"smoke-{uuid4().hex}@example.com",
        },
        token,
    )
    template = multipart(
        f"/api/v1/participants/{participant['id']}/enrollment", {"consent": "true"}, token
    )
    for _ in range(30):
        current = call(f"/api/v1/biometric-templates/{template['id']}", token=token, method="GET")
        if current["status"] != "pending" and current["status"] != "processing":
            break
        time.sleep(1)
    assert current["status"] == "ready", current["status"]
    attempt = multipart("/api/v1/access/check", {"event_id": str(event["id"])}, token)
    assert attempt["decision"] == "granted"
    deleted = call(
        f"/api/v1/participants/{participant['id']}/biometric", token=token, method="DELETE"
    )
    assert deleted["status"] == "deleted"
    print("HTTP → outbox → worker → проверка доступа → удаление: успешно")


if __name__ == "__main__":
    main()

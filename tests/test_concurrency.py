import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from visionpass import api
from visionpass.cv import DeterministicDemoEncoder


def test_health_responds_while_access_encoder_is_waiting(
    client, admin_headers, event_and_participant, monkeypatch
):
    event, _ = event_and_participant
    started, release = threading.Event(), threading.Event()
    in_event_loop = []

    class Slow(DeterministicDemoEncoder):
        def encode(self, image):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                in_event_loop.append(False)
            else:
                in_event_loop.append(True)
            started.set()
            assert release.wait(10)
            return super().encode(image)

    monkeypatch.setattr(api, "get_encoder", lambda: Slow())
    monkeypatch.setattr(api, "check_connection", lambda: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        access = pool.submit(
            client.post,
            "/api/v1/access/check",
            headers=admin_headers,
            data={"event_id": str(event["id"])},
            files={"photo": ("face.jpg", b"demo-face", "image/jpeg")},
        )
        try:
            assert started.wait(5)
            health = pool.submit(client.get, "/health")
            assert health.result(timeout=1).status_code == 200
            assert not access.done()
        finally:
            release.set()
            response = access.result(timeout=5)
        assert response.status_code == 201
    assert in_event_loop == [False]


@pytest.mark.parametrize("route", ["access", "enrollment"])
@pytest.mark.parametrize("phase", ["image", "service"])
def test_image_validation_and_database_service_run_outside_event_loop(
    phase, route, client, admin_headers, event_and_participant, monkeypatch
):
    event, participant = event_and_participant
    contexts = []

    def ensure_worker_thread(label):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            contexts.append((label, False))
        else:
            contexts.append((label, True))

    content = io.BytesIO()
    Image.new("RGB", (2, 2)).save(content, format="PNG")
    settings = api.get_settings().model_copy(update={"matcher_backend": "face_recognition"})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    original_open = api.Image.open

    def open_image(*args, **kwargs):
        if phase == "image":
            ensure_worker_thread("image")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(api.Image, "open", open_image)
    service_name = "verify_access" if route == "access" else "request_enrollment"
    original_service = getattr(api, service_name)

    def service(db, *args):
        if phase == "service":
            ensure_worker_thread("service")
        return original_service(db, *args)

    monkeypatch.setattr(api, service_name, service)
    url = (
        "/api/v1/access/check"
        if route == "access"
        else f"/api/v1/participants/{participant['id']}/enrollment"
    )
    data = {"event_id": str(event["id"])} if route == "access" else {"consent": "true"}
    response = client.post(
        url,
        headers=admin_headers,
        data=data,
        files={"photo": ("face.png", content.getvalue(), "image/png")},
    )
    assert response.status_code == (201 if route == "access" else 202)
    assert contexts == [(phase, False)]

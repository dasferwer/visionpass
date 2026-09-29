import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select, text

from visionpass import services
from visionpass.crypto import decrypt, encrypt
from visionpass.cv import DeterministicDemoEncoder
from visionpass.db import SessionLocal
from visionpass.models import AccessAttempt, BiometricTemplate, TemplateStatus, User, UserRole


def fixture_identity(event_and_participant):
    _, participant = event_and_participant
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.role == UserRole.ADMIN))
        assert actor is not None
        return actor.id, UUID(str(participant["id"]))


def enroll(actor_id, participant_id, image=b"face-v1"):
    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        assert actor is not None
        return services.request_enrollment(db, actor, participant_id, image, True)


def test_encryption_and_generation(event_and_participant):
    actor_id, participant_id = fixture_identity(event_and_participant)
    first = enroll(actor_id, participant_id)
    assert first.encrypted_image is not None and b"face-v1" not in first.encrypted_image
    assert first.pending_image is None and first.embedding is None
    assert decrypt(first.encrypted_image, first.id, first.generation, "image") == b"face-v1"
    with pytest.raises(InvalidTag):
        decrypt(first.encrypted_image, uuid4(), first.generation, "image")
    second = enroll(actor_id, participant_id, b"face-v2")
    assert second.id == first.id and second.generation == first.generation + 1
    with SessionLocal() as db:
        ready = services.process_enrollment(db, second.id, DeterministicDemoEncoder())
        assert ready.status == TemplateStatus.READY and ready.encrypted_image is None
        assert ready.encrypted_embedding is not None and ready.embedding is None
        assert ready.encoder_id == DeterministicDemoEncoder.model_id
        assert (
            db.scalar(
                text(
                    "SELECT pending_image IS NULL AND embedding IS NULL "
                    "FROM biometric_templates WHERE id=:id"
                ),
                {"id": second.id},
            )
            is True
        )


def test_deletion_wins_against_slow_encoder(event_and_participant):
    actor_id, participant_id = fixture_identity(event_and_participant)
    template = enroll(actor_id, participant_id)
    started, release = threading.Event(), threading.Event()

    class Slow(DeterministicDemoEncoder):
        def encode(self, image):
            started.set()
            assert release.wait(5)
            return super().encode(image)

    def process():
        with SessionLocal() as db:
            services.process_enrollment(db, template.id, Slow())

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(process)
        assert started.wait(5)
        with SessionLocal() as db:
            actor = db.get(User, actor_id)
            services.delete_biometric(db, actor, participant_id)
        release.set()
        task.result(timeout=5)
    with SessionLocal() as db:
        stored = db.get(BiometricTemplate, template.id)
        assert stored.status == TemplateStatus.DELETED
        assert stored.encrypted_image is None and stored.encrypted_embedding is None


def test_new_enrollment_wins_against_slow_encoder(event_and_participant):
    actor_id, participant_id = fixture_identity(event_and_participant)
    first = enroll(actor_id, participant_id)
    started, release = threading.Event(), threading.Event()

    class Slow(DeterministicDemoEncoder):
        def encode(self, image):
            started.set()
            assert release.wait(5)
            return super().encode(image)

    def process():
        with SessionLocal() as db:
            services.process_enrollment(db, first.id, Slow())

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(process)
        assert started.wait(5)
        latest = enroll(actor_id, participant_id, b"new-face")
        release.set()
        task.result(timeout=5)
    with SessionLocal() as db:
        stored = db.get(BiometricTemplate, first.id)
        assert stored.generation == latest.generation and stored.status == TemplateStatus.PENDING
        assert stored.encrypted_embedding is None
        assert (
            services.process_enrollment(db, first.id, DeterministicDemoEncoder()).status
            == TemplateStatus.READY
        )


def test_retention_and_deleted_review(event_and_participant):
    actor_id, participant_id = fixture_identity(event_and_participant)
    template = enroll(actor_id, participant_id)
    with SessionLocal.begin() as db:
        stored = db.get(BiometricTemplate, template.id)
        stored.image_expires_at = services.now_utc() - timedelta(seconds=1)
    with SessionLocal() as db:
        assert services.expire_sensitive_data(db) >= 1
        stored = db.get(BiometricTemplate, template.id)
        assert stored.encrypted_image is None and stored.status == TemplateStatus.REJECTED
    enroll(actor_id, participant_id, b"new-face")
    with SessionLocal() as db:
        services.process_enrollment(db, template.id, DeterministicDemoEncoder())
    with SessionLocal.begin() as db:
        stored = db.get(BiometricTemplate, template.id)
        stored.expires_at = services.now_utc() - timedelta(seconds=1)
    with SessionLocal() as db:
        assert services.expire_sensitive_data(db) >= 1
        stored = db.get(BiometricTemplate, template.id)
        assert stored.status == TemplateStatus.DELETED and stored.encrypted_embedding is None


def test_legacy_template_not_matched(event_and_participant):
    event, participant = event_and_participant
    actor_id, participant_id = fixture_identity(event_and_participant)
    template = enroll(actor_id, participant_id)
    with SessionLocal() as db:
        services.process_enrollment(db, template.id, DeterministicDemoEncoder())
    with SessionLocal.begin() as db:
        db.get(BiometricTemplate, template.id).encoder_id = "legacy-unverified"
    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        result = services.verify_access(
            db, actor, UUID(str(event["id"])), b"face-v1", DeterministicDemoEncoder()
        )
        assert result.participant_id is None and result.decision == "denied"


def test_crypto_context_rejects_copy(event_and_participant):
    actor_id, participant_id = fixture_identity(event_and_participant)
    template = enroll(actor_id, participant_id)
    with pytest.raises(InvalidTag):
        decrypt(template.encrypted_image, template.id, template.generation + 1, "image")
    assert (
        decrypt(
            encrypt(b"payload", template.id, template.generation, "embedding"),
            template.id,
            template.generation,
            "embedding",
        )
        == b"payload"
    )


def test_access_race_with_deletion(event_and_participant):
    event, _ = event_and_participant
    actor_id, participant_id = fixture_identity(event_and_participant)
    template = enroll(actor_id, participant_id)
    with SessionLocal() as db:
        services.process_enrollment(db, template.id, DeterministicDemoEncoder())
    started, release = threading.Event(), threading.Event()

    class Slow(DeterministicDemoEncoder):
        def encode(self, image):
            started.set()
            assert release.wait(5)
            return super().encode(image)

    def check():
        with SessionLocal() as db:
            actor = db.get(User, actor_id)
            return services.verify_access(db, actor, UUID(str(event["id"])), b"face-v1", Slow())

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(check)
        assert started.wait(5)
        with SessionLocal() as db:
            services.delete_biometric(db, db.get(User, actor_id), participant_id)
        release.set()
        result = task.result(timeout=5)
    assert result.decision == "denied" and result.participant_id is None


def test_worker_quarantines_invalid_message():
    from visionpass.broker import ENROLLMENT_QUEUE, INVALID_QUEUE, connect, declare_topology
    from visionpass.config import get_settings
    from visionpass.workers.enrollment import consume_one

    assert "rabbitmq-test" in get_settings().rabbitmq_url
    connection = connect()
    try:
        channel = connection.channel()
        declare_topology(channel)
        channel.confirm_delivery()
        channel.queue_purge(ENROLLMENT_QUEUE)
        channel.queue_purge(INVALID_QUEUE)
        for body in [b"{broken", b"null", b'{"event_id":1,"template_id":2,"generation":true}']:
            channel.basic_publish(
                exchange="", routing_key=ENROLLMENT_QUEUE, body=body, mandatory=True
            )
            assert consume_one(channel)
            method, _, returned = channel.basic_get(INVALID_QUEUE, auto_ack=True)
            assert method is not None and returned == body
    finally:
        connection.close()


def test_demo_deletion_removes_attempt_links(event_and_participant):
    event, _ = event_and_participant
    actor_id, participant_id = fixture_identity(event_and_participant)
    template = enroll(actor_id, participant_id)
    with SessionLocal() as db:
        services.process_enrollment(db, template.id, DeterministicDemoEncoder())
        actor = db.get(User, actor_id)
        attempt = services.verify_access(
            db, actor, UUID(str(event["id"])), b"face-v1", DeterministicDemoEncoder()
        )
        attempt_id = attempt.id
        services.delete_demo_participant(db, actor, participant_id)
    with SessionLocal() as db:
        stored = db.get(AccessAttempt, attempt_id)
        assert stored.participant_id is None and stored.distance is None
        assert stored.confidence is None and stored.reason == "Данные участника удалены"

import json
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visionpass.config import get_settings
from visionpass.crypto import decrypt, encrypt
from visionpass.cv import FaceEncoder, FaceEncodingError, euclidean_distance
from visionpass.models import (
    AccessAttempt,
    AccessDecision,
    AuditLog,
    BiometricTemplate,
    Event,
    ManualReview,
    OutboxEvent,
    Participant,
    TemplateStatus,
    User,
)
from visionpass.schemas import EventCreate, ParticipantCreate, ReviewCreate


def now_utc() -> datetime:
    return datetime.now(UTC)


def audit(
    db: Session,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def create_event(db: Session, actor: User, data: EventCreate) -> Event:
    event = Event(name=data.name, starts_at=data.starts_at, ends_at=data.ends_at)
    db.add(event)
    db.flush()
    audit(db, actor.id, "event.created", "event", event.id)
    db.commit()
    db.refresh(event)
    return event


def get_event(db: Session, event_id: UUID) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return event


def create_participant(
    db: Session, actor: User, event_id: UUID, data: ParticipantCreate
) -> Participant:
    get_event(db, event_id)
    participant = Participant(
        event_id=event_id,
        external_id=data.external_id,
        full_name=data.full_name,
        email=str(data.email).lower(),
    )
    db.add(participant)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) not in {
            "participants_event_external_key",
            "participants_event_email_key",
        }:
            raise
        raise HTTPException(status_code=409, detail="Участник уже зарегистрирован") from exc
    audit(db, actor.id, "participant.created", "participant", participant.id)
    db.commit()
    db.refresh(participant)
    return participant


def get_participant(db: Session, participant_id: UUID) -> Participant:
    participant = db.get(Participant, participant_id)
    if participant is None or participant.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    return participant


def lock_participant_template(
    db: Session, participant_id: UUID
) -> tuple[Participant, BiometricTemplate | None]:
    participant = db.scalar(
        select(Participant)
        .where(Participant.id == participant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if participant is None or participant.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    template = db.scalar(
        select(BiometricTemplate)
        .where(BiometricTemplate.participant_id == participant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return participant, template


def request_enrollment(
    db: Session, actor: User, participant_id: UUID, image: bytes, consent: bool
) -> BiometricTemplate:
    if not consent:
        raise HTTPException(status_code=422, detail="Нужно явное согласие на обработку биометрии")
    participant, template = lock_participant_template(db, participant_id)
    now = now_utc()
    if template is None:
        template = BiometricTemplate(
            participant_id=participant.id,
            status=TemplateStatus.PENDING,
            consent_at=now,
            generation=1,
        )
        db.add(template)
        db.flush()
    else:
        template.generation += 1
    generation = template.generation
    template.status = TemplateStatus.PENDING
    template.encrypted_image = encrypt(image, template.id, generation, "image")
    template.encrypted_embedding = None
    template.embedding = None
    template.pending_image = None
    template.encoder_id = None
    template.consent_at = now
    template.image_expires_at = now + timedelta(seconds=get_settings().image_retention_seconds)
    template.expires_at = now + timedelta(days=get_settings().template_retention_days)
    template.processed_at = template.deleted_at = template.lease_until = None
    template.lease_token = None
    template.attempts = 0
    template.error = None
    db.add(
        OutboxEvent(
            event_type="biometric.enrollment.requested",
            payload={"template_id": str(template.id), "generation": generation},
        )
    )
    audit(
        db,
        actor.id,
        "biometric.enrollment.requested",
        "biometric_template",
        template.id,
        {"generation": generation, "consent_recorded": True},
    )
    db.commit()
    db.refresh(template)
    return template


def get_template(db: Session, template_id: UUID) -> BiometricTemplate:
    template = db.get(BiometricTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Биометрический шаблон не найден")
    return template


def process_enrollment(db: Session, template_id: UUID, encoder: FaceEncoder) -> BiometricTemplate:
    template = db.get(BiometricTemplate, template_id)
    if template is None:
        raise LookupError("Шаблон не найден")
    participant, template = lock_participant_template(db, template.participant_id)
    assert template is not None
    now = now_utc()
    if template.status in {TemplateStatus.READY, TemplateStatus.REJECTED, TemplateStatus.DELETED}:
        return template
    if template.lease_until is not None and template.lease_until > now:
        return template
    if (
        template.image_expires_at is None
        or template.image_expires_at <= now
        or template.encrypted_image is None
    ):
        template.encrypted_image = None
        template.image_expires_at = None
        template.status = TemplateStatus.REJECTED
        template.error = "Истёк срок хранения фотографии"
        template.lease_token = template.lease_until = None
        audit(db, None, "biometric.enrollment.expired", "biometric_template", template.id)
        db.commit()
        return template
    generation = template.generation
    encrypted_image = template.encrypted_image
    token = uuid4()
    template.lease_token = token
    template.lease_until = now + timedelta(seconds=get_settings().enrollment_lease_seconds)
    template.attempts += 1
    template.status = TemplateStatus.PROCESSING
    db.commit()
    error = None
    vector = None
    try:
        image = decrypt(encrypted_image, template_id, generation, "image")
        vector = encoder.encode(image)
        if not vector or not all(math.isfinite(value) for value in vector):
            raise FaceEncodingError("Некорректный биометрический вектор")
    except FaceEncodingError:
        error = "Не удалось создать шаблон из фотографии"
    except Exception:
        # Аренда останется до истечения срока: другой worker сможет повторить попытку.
        raise
    participant, template = lock_participant_template(db, participant.id)
    assert template is not None
    if (
        template.generation != generation
        or template.lease_token != token
        or template.status != TemplateStatus.PROCESSING
    ):
        db.rollback()
        return template
    now = now_utc()
    if template.expires_at is None or template.expires_at <= now:
        error = "Истёк срок хранения шаблона"
    if template.image_expires_at is None or template.image_expires_at <= now:
        error = "Истёк срок хранения фотографии"
    if error is not None:
        template.status = TemplateStatus.REJECTED
        template.encrypted_embedding = None
        template.error = error
        action = "biometric.enrollment.rejected"
    else:
        assert vector is not None
        template.encrypted_embedding = encrypt(
            json.dumps(vector).encode(), template.id, generation, "embedding"
        )
        template.encoder_id = encoder.model_id
        template.status = TemplateStatus.READY
        template.error = None
        action = "biometric.enrollment.completed"
    template.encrypted_image = None
    template.image_expires_at = None
    template.pending_image = None
    template.embedding = None
    template.lease_token = template.lease_until = None
    template.processed_at = now
    audit(db, None, action, "biometric_template", template.id, {"generation": generation})
    db.commit()
    db.refresh(template)
    return template


def process_next_enrollment(db: Session, encoder: FaceEncoder) -> bool:
    now = now_utc()
    template_id = db.scalar(
        select(BiometricTemplate.id)
        .where(
            BiometricTemplate.status.in_([TemplateStatus.PENDING, TemplateStatus.PROCESSING]),
            (BiometricTemplate.lease_until.is_(None) | (BiometricTemplate.lease_until <= now)),
        )
        .order_by(BiometricTemplate.created_at, BiometricTemplate.id)
        .limit(1)
    )
    if template_id is None:
        return False
    process_enrollment(db, template_id, encoder)
    return True


def verify_access(
    db: Session, actor: User, event_id: UUID, image: bytes, encoder: FaceEncoder
) -> AccessAttempt:
    get_event(db, event_id)
    probe = encoder.encode(image)
    if not probe or not all(math.isfinite(value) for value in probe):
        raise FaceEncodingError("Некорректный биометрический вектор")
    settings = get_settings()
    now = now_utc()
    rows = db.execute(
        select(BiometricTemplate, Participant)
        .join(Participant)
        .where(
            Participant.event_id == event_id,
            Participant.deleted_at.is_(None),
            BiometricTemplate.status == TemplateStatus.READY,
            BiometricTemplate.encoder_id == encoder.model_id,
            BiometricTemplate.expires_at > now,
        )
        .limit(settings.max_match_candidates + 1)
    ).all()
    if len(rows) > settings.max_match_candidates:
        raise HTTPException(status_code=503, detail="Превышен предел сравнения шаблонов")
    snapshots = [
        (template.id, template.generation, template.encrypted_embedding, participant.id)
        for template, participant in rows
    ]
    db.commit()
    candidates = []
    for template_id, generation, encrypted, participant_id in snapshots:
        if encrypted is None:
            continue
        vector = json.loads(decrypt(encrypted, template_id, generation, "embedding"))
        candidates.append(
            (euclidean_distance(probe, vector), template_id, generation, participant_id)
        )
    distance: float | None
    confidence: float | None
    if candidates:
        distance, template_id, generation, participant_id = min(candidates)
        participant, current = lock_participant_template(db, participant_id)
        if (
            current is None
            or current.id != template_id
            or current.generation != generation
            or current.status != TemplateStatus.READY
            or current.expires_at is None
            or current.expires_at <= now_utc()
            or current.encoder_id != encoder.model_id
        ):
            raise HTTPException(status_code=409, detail="Шаблон изменился; повторите проверку")
        confidence = max(0.0, min(1.0, 1.0 - distance))
        if distance <= settings.match_threshold:
            decision, reason = AccessDecision.GRANTED, "Совпадение в пределах порога"
        elif distance <= settings.match_threshold + settings.review_margin:
            decision, reason = AccessDecision.REVIEW, "Требуется ручная проверка"
        else:
            decision, reason = AccessDecision.DENIED, "Совпадений в пределах порога нет"
            participant_id = None
            generation = None
    else:
        distance = confidence = None
        decision, reason = AccessDecision.DENIED, "Подходящих активных шаблонов нет"
        participant_id = generation = None
    attempt = AccessAttempt(
        event_id=event_id,
        participant_id=participant_id,
        template_generation=generation,
        encoder_id=encoder.model_id,
        decision=decision,
        distance=distance,
        confidence=confidence,
        reason=reason,
    )
    db.add(attempt)
    db.flush()
    audit(
        db,
        actor.id,
        "access.checked",
        "access_attempt",
        attempt.id,
        {"decision": str(decision), "raw_image_stored": False},
    )
    db.commit()
    db.refresh(attempt)
    return attempt


def review_attempt(
    db: Session, reviewer: User, attempt_id: UUID, data: ReviewCreate
) -> ManualReview:
    attempt = db.get(AccessAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Попытка не найдена")
    if attempt.participant_id is None:
        raise HTTPException(status_code=409, detail="Участник удалён")
    _, template = lock_participant_template(db, attempt.participant_id)
    attempt = db.scalar(
        select(AccessAttempt)
        .where(AccessAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert attempt is not None
    if (
        attempt.decision != AccessDecision.REVIEW
        or db.scalar(select(ManualReview).where(ManualReview.attempt_id == attempt_id)) is not None
    ):
        raise HTTPException(
            status_code=409, detail="Попытка уже рассмотрена или не требует проверки"
        )
    if (
        template is None
        or template.status != TemplateStatus.READY
        or template.generation != attempt.template_generation
        or template.expires_at is None
        or template.expires_at <= now_utc()
    ):
        raise HTTPException(status_code=409, detail="Шаблон изменился или истёк")
    review = ManualReview(
        attempt_id=attempt.id,
        reviewer_id=reviewer.id,
        resolution=AccessDecision(data.resolution),
        note=data.note,
    )
    db.add(review)
    db.flush()
    audit(
        db,
        reviewer.id,
        "access.reviewed",
        "access_attempt",
        attempt.id,
        {"resolution": data.resolution},
    )
    db.commit()
    db.refresh(review)
    return review


def remove_biometric_scores(db: Session, participant_id: UUID) -> None:
    db.execute(
        update(AccessAttempt)
        .where(AccessAttempt.participant_id == participant_id)
        .values(distance=None, confidence=None, template_generation=None, encoder_id=None)
    )


def clear_template(template: BiometricTemplate, now: datetime) -> None:
    template.generation += 1
    template.encrypted_embedding = template.encrypted_image = None
    template.embedding = template.pending_image = None
    template.image_expires_at = template.lease_until = None
    template.lease_token = None
    template.status = TemplateStatus.DELETED
    template.deleted_at = now
    template.encoder_id = None


def delete_biometric(db: Session, actor: User, participant_id: UUID) -> BiometricTemplate:
    _, template = lock_participant_template(db, participant_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Биометрический шаблон не найден")
    clear_template(template, now_utc())
    remove_biometric_scores(db, participant_id)
    audit(db, actor.id, "biometric.deleted", "biometric_template", template.id)
    db.commit()
    db.refresh(template)
    return template


def delete_demo_participant(db: Session, actor: User, participant_id: UUID) -> None:
    participant, template = lock_participant_template(db, participant_id)
    if template is not None:
        clear_template(template, now_utc())
    attempt_ids = select(AccessAttempt.id).where(AccessAttempt.participant_id == participant.id)
    db.execute(
        update(ManualReview)
        .where(ManualReview.attempt_id.in_(attempt_ids))
        .values(note="Удалено по запросу")
    )
    db.execute(
        update(AccessAttempt)
        .where(AccessAttempt.participant_id == participant.id)
        .values(
            participant_id=None,
            distance=None,
            confidence=None,
            template_generation=None,
            reason="Данные участника удалены",
        )
    )
    participant.full_name = "Удалённый участник"
    participant.email = f"deleted-{participant.id}@example.invalid"
    participant.external_id = f"deleted-{participant.id}"
    participant.deleted_at = now_utc()
    audit(db, actor.id, "participant.demo_data_deleted", "participant", participant.id)
    db.commit()


def expire_sensitive_data(db: Session, limit: int = 100) -> int:
    now = now_utc()
    ids = list(
        db.scalars(
            select(BiometricTemplate.id)
            .where(
                (
                    (BiometricTemplate.image_expires_at <= now)
                    & BiometricTemplate.encrypted_image.is_not(None)
                )
                | (
                    (BiometricTemplate.expires_at <= now)
                    & (
                        BiometricTemplate.encrypted_embedding.is_not(None)
                        | BiometricTemplate.encrypted_image.is_not(None)
                    )
                )
            )
            .order_by(BiometricTemplate.image_expires_at, BiometricTemplate.id)
            .limit(limit)
        )
    )
    cleaned = 0
    for template_id in ids:
        template = db.get(BiometricTemplate, template_id)
        if template is None:
            continue
        try:
            _, template = lock_participant_template(db, template.participant_id)
        except HTTPException:
            db.rollback()
            continue
        assert template is not None
        if template.expires_at is not None and template.expires_at <= now:
            clear_template(template, now)
            remove_biometric_scores(db, template.participant_id)
            audit(db, None, "biometric.expired", "biometric_template", template.id)
            cleaned += 1
        elif template.image_expires_at is not None and template.image_expires_at <= now:
            template.encrypted_image = None
            template.image_expires_at = None
            if template.status in {TemplateStatus.PENDING, TemplateStatus.PROCESSING}:
                template.generation += 1
                template.status = TemplateStatus.REJECTED
                template.error = "Истёк срок хранения фотографии"
                template.lease_token = template.lease_until = None
            audit(db, None, "biometric.image_expired", "biometric_template", template.id)
            cleaned += 1
        db.commit()
    return cleaned

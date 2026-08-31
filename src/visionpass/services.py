from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visionpass.config import get_settings
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
        raise HTTPException(status_code=404, detail="Event not found")
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
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Participant already exists") from None
    audit(db, actor.id, "participant.created", "participant", participant.id)
    db.commit()
    db.refresh(participant)
    return participant


def get_participant(db: Session, participant_id: UUID) -> Participant:
    participant = db.get(Participant, participant_id)
    if participant is None or participant.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Participant not found")
    return participant


def request_enrollment(
    db: Session,
    actor: User,
    participant_id: UUID,
    image: bytes,
    consent: bool,
) -> BiometricTemplate:
    if not consent:
        raise HTTPException(status_code=422, detail="Explicit biometric consent is required")
    participant = get_participant(db, participant_id)
    template = db.scalar(
        select(BiometricTemplate).where(BiometricTemplate.participant_id == participant.id)
    )
    if template is None:
        template = BiometricTemplate(
            participant_id=participant.id,
            status=TemplateStatus.PENDING,
            pending_image=image,
            consent_at=now_utc(),
        )
        db.add(template)
        db.flush()
    else:
        template.status = TemplateStatus.PENDING
        template.pending_image = image
        template.embedding = None
        template.consent_at = now_utc()
        template.processed_at = None
        template.deleted_at = None
        template.error = None
    db.add(
        OutboxEvent(
            event_type="biometric.enrollment.requested",
            payload={"template_id": str(template.id)},
        )
    )
    audit(
        db,
        actor.id,
        "biometric.enrollment.requested",
        "biometric_template",
        template.id,
        {"consent_recorded": True},
    )
    db.commit()
    db.refresh(template)
    return template


def get_template(db: Session, template_id: UUID) -> BiometricTemplate:
    template = db.get(BiometricTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Biometric template not found")
    return template


def process_enrollment(db: Session, template_id: UUID, encoder: FaceEncoder) -> BiometricTemplate:
    template = db.scalar(
        select(BiometricTemplate).where(BiometricTemplate.id == template_id).with_for_update()
    )
    if template is None:
        raise LookupError("Biometric template not found")
    if template.status in {TemplateStatus.READY, TemplateStatus.REJECTED, TemplateStatus.DELETED}:
        return template
    template.status = TemplateStatus.PROCESSING
    image = template.pending_image
    try:
        if image is None:
            raise FaceEncodingError("Pending enrollment image is missing")
        template.embedding = encoder.encode(image)
        template.status = TemplateStatus.READY
        template.error = None
        action = "biometric.enrollment.completed"
    except FaceEncodingError as exc:
        template.embedding = None
        template.status = TemplateStatus.REJECTED
        template.error = str(exc)[:1000]
        action = "biometric.enrollment.rejected"
    finally:
        template.pending_image = None
        template.processed_at = now_utc()
    audit(db, None, action, "biometric_template", template.id)
    db.commit()
    db.refresh(template)
    return template


def verify_access(
    db: Session,
    actor: User,
    event_id: UUID,
    image: bytes,
    encoder: FaceEncoder,
) -> AccessAttempt:
    get_event(db, event_id)
    probe = encoder.encode(image)
    rows = db.execute(
        select(BiometricTemplate, Participant)
        .join(Participant, Participant.id == BiometricTemplate.participant_id)
        .where(
            Participant.event_id == event_id,
            Participant.deleted_at.is_(None),
            BiometricTemplate.status == TemplateStatus.READY,
        )
    ).all()
    candidates = [
        (euclidean_distance(probe, template.embedding or []), participant)
        for template, participant in rows
    ]
    settings = get_settings()
    if not candidates:
        decision = AccessDecision.DENIED
        participant = None
        distance = None
        confidence = None
        reason = "No active biometric templates"
    else:
        distance, nearest = min(candidates, key=lambda item: item[0])
        confidence = max(0.0, min(1.0, 1.0 - distance))
        if distance <= settings.match_threshold:
            decision = AccessDecision.GRANTED
            participant = nearest
            reason = "Match within configured threshold"
        elif distance <= settings.match_threshold + settings.review_margin:
            decision = AccessDecision.REVIEW
            participant = nearest
            reason = "Uncertain match requires manual review"
        else:
            decision = AccessDecision.DENIED
            participant = None
            reason = "No match within configured threshold"
    attempt = AccessAttempt(
        event_id=event_id,
        participant_id=participant.id if participant else None,
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
        raise HTTPException(status_code=404, detail="Access attempt not found")
    if attempt.decision != AccessDecision.REVIEW:
        raise HTTPException(status_code=409, detail="Only uncertain attempts can be reviewed")
    review = ManualReview(
        attempt_id=attempt.id,
        reviewer_id=reviewer.id,
        resolution=AccessDecision(data.resolution),
        note=data.note,
    )
    db.add(review)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Attempt is already reviewed") from None
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


def delete_biometric(db: Session, actor: User, participant_id: UUID) -> BiometricTemplate:
    participant = get_participant(db, participant_id)
    template = db.scalar(
        select(BiometricTemplate).where(BiometricTemplate.participant_id == participant.id)
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Biometric template not found")
    template.embedding = None
    template.pending_image = None
    template.status = TemplateStatus.DELETED
    template.deleted_at = now_utc()
    audit(db, actor.id, "biometric.deleted", "biometric_template", template.id)
    db.commit()
    db.refresh(template)
    return template


def delete_demo_participant(db: Session, actor: User, participant_id: UUID) -> None:
    participant = get_participant(db, participant_id)
    template = db.scalar(
        select(BiometricTemplate).where(BiometricTemplate.participant_id == participant.id)
    )
    if template:
        template.embedding = None
        template.pending_image = None
        template.status = TemplateStatus.DELETED
        template.deleted_at = now_utc()
    participant.full_name = "Deleted participant"
    participant.email = f"deleted-{participant.id}@example.invalid"
    participant.external_id = f"deleted-{participant.id}"
    participant.deleted_at = now_utc()
    audit(db, actor.id, "participant.demo_data_deleted", "participant", participant.id)
    db.commit()

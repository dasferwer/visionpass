import io
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, text

from visionpass.broker import check_connection
from visionpass.config import get_settings
from visionpass.cv import FaceEncodingError, get_encoder
from visionpass.dependencies import AdminUser, CurrentUser, DbSession
from visionpass.models import AccessAttempt, AccessDecision, AuditLog, User
from visionpass.schemas import (
    AccessAttemptRead,
    AuditRead,
    EventCreate,
    EventRead,
    LoginRequest,
    ParticipantCreate,
    ParticipantRead,
    ReviewCreate,
    ReviewRead,
    TemplateRead,
    TokenResponse,
    UserRead,
)
from visionpass.security import create_access_token, verify_password
from visionpass.services import (
    create_event,
    create_participant,
    delete_biometric,
    delete_demo_participant,
    get_template,
    request_enrollment,
    review_attempt,
    verify_access,
)

router = APIRouter()


def read_image(photo: UploadFile) -> bytes:
    if photo.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=415, detail="JPEG or PNG image required")
    image = photo.file.read(get_settings().max_image_bytes + 1)
    if not image:
        raise HTTPException(status_code=422, detail="Image is empty")
    if len(image) > get_settings().max_image_bytes:
        raise HTTPException(status_code=413, detail="Image is too large")
    if get_settings().matcher_backend != "stub":
        try:
            with Image.open(io.BytesIO(image)) as opened:
                if (
                    opened.format not in {"JPEG", "PNG"}
                    or opened.width * opened.height > get_settings().max_image_pixels
                ):
                    raise ValueError("Недопустимый формат или размер изображения")
                opened.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(
                status_code=422, detail="Повреждённое или слишком большое изображение"
            ) from None
    return image


@router.get("/health", tags=["service"])
def health(db: DbSession) -> dict[str, str]:
    database = "ok"
    rabbitmq = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    try:
        check_connection()
    except Exception:
        rabbitmq = "error"
    if database != "ok" or rabbitmq != "ok":
        raise HTTPException(
            status_code=503,
            detail={"database": database, "rabbitmq": rabbitmq},
        )
    return {
        "status": "ok",
        "database": database,
        "rabbitmq": rabbitmq,
        "matcher_backend": get_settings().matcher_backend,
    }


@router.post("/api/v1/auth/login", response_model=TokenResponse, tags=["auth"])
def login(data: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id, str(user.role)))


@router.get("/api/v1/users/me", response_model=UserRead, tags=["auth"])
def current_user(user: CurrentUser) -> User:
    return user


@router.post(
    "/api/v1/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    tags=["events"],
)
def create_event_endpoint(data: EventCreate, db: DbSession, admin: AdminUser) -> object:
    return create_event(db, admin, data)


@router.post(
    "/api/v1/events/{event_id}/participants",
    response_model=ParticipantRead,
    status_code=status.HTTP_201_CREATED,
    tags=["participants"],
)
def create_participant_endpoint(
    event_id: UUID, data: ParticipantCreate, db: DbSession, user: CurrentUser
) -> object:
    return create_participant(db, user, event_id, data)


@router.post(
    "/api/v1/participants/{participant_id}/enrollment",
    response_model=TemplateRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["biometrics"],
)
def enroll_participant(
    participant_id: UUID,
    db: DbSession,
    user: CurrentUser,
    photo: Annotated[UploadFile, File()],
    consent: Annotated[bool, Form()],
) -> object:
    image = read_image(photo)
    return request_enrollment(db, user, participant_id, image, consent)


@router.get(
    "/api/v1/biometric-templates/{template_id}",
    response_model=TemplateRead,
    tags=["biometrics"],
)
def read_template(template_id: UUID, db: DbSession, user: CurrentUser) -> object:
    del user
    return get_template(db, template_id)


@router.delete(
    "/api/v1/participants/{participant_id}/biometric",
    response_model=TemplateRead,
    tags=["biometrics"],
)
def delete_biometric_endpoint(participant_id: UUID, db: DbSession, admin: AdminUser) -> object:
    return delete_biometric(db, admin, participant_id)


@router.delete(
    "/api/v1/participants/{participant_id}/demo-data",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["privacy"],
)
def delete_demo_data(participant_id: UUID, db: DbSession, admin: AdminUser) -> None:
    delete_demo_participant(db, admin, participant_id)


@router.post(
    "/api/v1/access/check",
    response_model=AccessAttemptRead,
    status_code=status.HTTP_201_CREATED,
    tags=["access"],
)
def check_access(
    db: DbSession,
    user: CurrentUser,
    event_id: Annotated[UUID, Form()],
    photo: Annotated[UploadFile, File()],
) -> object:
    image = read_image(photo)
    try:
        return verify_access(db, user, event_id, image, get_encoder())
    except FaceEncodingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get(
    "/api/v1/access/attempts",
    response_model=list[AccessAttemptRead],
    tags=["access"],
)
def list_attempts(
    db: DbSession,
    user: CurrentUser,
    decision: AccessDecision | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AccessAttempt]:
    del user
    statement = select(AccessAttempt)
    if decision is not None:
        statement = statement.where(AccessAttempt.decision == decision)
    return list(db.scalars(statement.order_by(AccessAttempt.created_at.desc()).limit(limit)))


@router.post(
    "/api/v1/access/attempts/{attempt_id}/review",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
    tags=["access"],
)
def review_access_attempt(
    attempt_id: UUID, data: ReviewCreate, db: DbSession, reviewer: CurrentUser
) -> object:
    return review_attempt(db, reviewer, attempt_id, data)


@router.get("/api/v1/audit", response_model=list[AuditRead], tags=["audit"])
def list_audit(
    db: DbSession,
    admin: AdminUser,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditLog]:
    del admin
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)))

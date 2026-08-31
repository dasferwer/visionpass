from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from visionpass.db import get_db
from visionpass.models import User, UserRole
from visionpass.security import decode_access_token

DbSession = Annotated[Session, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise error
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = UUID(str(payload.get("sub")))
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise error from None
    user = db.get(User, user_id)
    if user is None:
        raise error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]

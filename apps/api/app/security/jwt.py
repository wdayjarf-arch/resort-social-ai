"""JWT access tokens with server-side session tracking and revocation."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.identity import AuthSession, User


def issue_access_token(db: Session, user: User) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(user.id),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.app_name,
        "typ": "access",
    }
    token = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    db.add(AuthSession(user_id=user.id, jti=jti, expires_at=expires_at))
    db.flush()
    return token, expires_at


def decode_and_validate_access_token(db: Session, token: str) -> User:
    settings = get_settings()
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
            options={"require": ["sub", "jti", "iat", "exp", "iss", "typ"]},
        )
        if claims.get("typ") != "access":
            raise credentials_error
        user_id = uuid.UUID(claims["sub"])
        jti = claims["jti"]
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise credentials_error

    now = datetime.now(timezone.utc)
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.jti == jti,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    user = db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if session is None or user is None:
        raise credentials_error
    return user


def revoke_access_token(db: Session, token: str) -> None:
    """Revoke the session represented by a valid token; idempotent for unknown tokens."""
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
            options={"require": ["sub", "jti", "exp", "iss", "typ"]},
        )
        if claims.get("typ") != "access":
            return
        session = db.scalar(select(AuthSession).where(AuthSession.jti == claims["jti"]))
        if session and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        return

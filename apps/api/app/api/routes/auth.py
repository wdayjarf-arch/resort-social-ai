from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.dependencies import get_db
from app.db.models.identity import AuthSession, User
from app.schemas.security import RegisterRequest, TokenResponse, UserRead
from app.security.jwt import issue_access_token, revoke_access_token
from app.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])
bearer = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    """Create an unprivileged user account. Organization membership is granted separately."""
    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email is already registered")
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    email = form.username.strip().lower()
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    # Keep the same response for unknown users and incorrect passwords.
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, expires_at = issue_access_token(db, user)
    db.commit()
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: str = Depends(bearer),
    db: Session = Depends(get_db),
) -> None:
    revoke_access_token(db, token)
    db.commit()
    return None

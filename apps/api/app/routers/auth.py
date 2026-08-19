from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.google_auth import GoogleAuthError, verify_google_id_token
from app.models import Subscription, User
from app.schemas import (
    GoogleLoginRequest,
    Token,
    UserCreate,
    UserLogin,
    UserOut,
    UserUpdate,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email уже зарегистрирован"
        )

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id))
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> Token:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is not None and user.hashed_password is None:
        # Google-created account: no password exists to check. The
        # message intentionally names the real fix -- /auth/register
        # already reveals email existence via its 409, so this hint
        # doesn't leak anything new.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Этот аккаунт создан через Google — используйте кнопку «Войти через Google»",
        )
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль"
        )
    return Token(access_token=create_access_token(user.id))


@router.post("/google", response_model=Token)
def login_with_google(payload: GoogleLoginRequest, db: Session = Depends(get_db)) -> Token:
    if not get_settings().google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Вход через Google не настроен на сервере",
        )
    try:
        claims = verify_google_id_token(payload.id_token)
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    email = claims["email"]
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, hashed_password=None)
        db.add(user)
        db.flush()
        db.add(Subscription(user_id=user.id))
        db.commit()
        db.refresh(user)
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    current_user.role = payload.role
    db.commit()
    db.refresh(current_user)
    return current_user

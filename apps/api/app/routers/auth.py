from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.security import (
    LOGIN_LOCKOUT_MINUTES,
    create_access_token,
    hash_password,
    is_locked_out,
    record_failed_login,
    record_successful_login,
    verify_password,
)

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
    try:
        db.flush()
        db.add(Subscription(user_id=user.id))
        db.commit()
    except IntegrityError:
        # A second registration for the same email raced past the check
        # above (e.g. a double-clicked submit) -- the email's UNIQUE
        # constraint is the real guard; without this, that race would
        # surface as an unhandled 500 instead of the same clean 409.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email уже зарегистрирован"
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> Token:
    user = db.scalar(select(User).where(User.email == payload.email))
    # CIN-159: checked before anything password-related so a locked
    # account gets the same 429 regardless of whether the submitted
    # password happens to be right -- otherwise a correct guess mid-
    # lockout would still leak "that was the password" via the 200.
    if user is not None and is_locked_out(user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Слишком много неудачных попыток входа — "
                f"попробуйте снова через {LOGIN_LOCKOUT_MINUTES} минут"
            ),
        )
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
        if user is not None:
            record_failed_login(user)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль"
        )
    record_successful_login(user)
    db.commit()
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
        try:
            db.flush()
            db.add(Subscription(user_id=user.id))
            db.commit()
        except IntegrityError:
            # Two concurrent Google sign-ins for the same brand-new
            # email raced past the check above -- the other request
            # already created the account, so use it rather than
            # erroring what's still a legitimate concurrent login.
            db.rollback()
            user = db.scalar(select(User).where(User.email == email))
        else:
            db.refresh(user)
    elif user.hashed_password is not None:
        # CIN-140 -- account pre-hijacking defence. /auth/register does
        # not verify the address (there is no mail infrastructure), so
        # anyone can register somebody else's email with a password of
        # their choosing. Without this branch, the real owner's first
        # "Sign in with Google" would drop them straight into that
        # attacker-created account, which the attacker still holds the
        # password to -- and this app stores connected social accounts
        # and can publish through them.
        #
        # Google has just proven control of the address; the password
        # holder never proved anything, so the password is dropped and
        # the account becomes Google-only. A legitimate user who set
        # that password themselves keeps full access through the same
        # Google account (/auth/login already tells them where to go).
        user.hashed_password = None
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

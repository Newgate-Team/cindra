from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не авторизован",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise unauthorized from exc

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Gate for endpoints exposing data about the whole user base
    (CIN-147). Checks `is_admin`, never `role` -- the latter is picked
    by the user at registration and editable from their profile, so it
    grants nothing.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав"
        )
    return current_user

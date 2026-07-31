from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import SocialAccount, User
from app.schemas import SocialAccountOut

router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])


@router.get("", response_model=list[SocialAccountOut])
def list_social_accounts(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SocialAccount]:
    return list(
        db.scalars(select(SocialAccount).where(SocialAccount.user_id == current_user.id))
    )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_social_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    result = db.execute(
        delete(SocialAccount).where(
            SocialAccount.id == account_id, SocialAccount.user_id == current_user.id
        )
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Аккаунт не найден")

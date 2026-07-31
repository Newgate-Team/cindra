from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Subscription, User
from app.schemas import SubscriptionOut

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Subscription:
    return db.scalar(select(Subscription).where(Subscription.user_id == current_user.id))

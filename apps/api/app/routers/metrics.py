from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.metrics import (
    average_time_to_first_post_seconds,
    publish_success_rate,
    retention,
)
from app.models import User
from app.schemas import MetricsSummary

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/summary", response_model=MetricsSummary)
def get_metrics_summary(
    # Authenticated, but not yet admin-restricted -- there's no admin
    # role in the User model. Fine for a 2-person team's own MVP
    # dashboard; needs a real access check before this is exposed
    # beyond that (see README.md metrics: "≥500 установок").
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetricsSummary:
    return MetricsSummary(
        average_time_to_first_post_seconds=average_time_to_first_post_seconds(db),
        retention_d7=retention(db, days=7),
        retention_d30=retention(db, days=30),
        publish_success_rate=publish_success_rate(db),
    )

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
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
    # Staff only (CIN-147): this aggregates the whole user base, and
    # registration is open and unverified (spec §6), so plain
    # authentication would have published retention and conversion to
    # anyone who signed up.
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MetricsSummary:
    return MetricsSummary(
        average_time_to_first_post_seconds=average_time_to_first_post_seconds(db),
        retention_d7=retention(db, days=7),
        retention_d30=retention(db, days=30),
        publish_success_rate=publish_success_rate(db),
    )

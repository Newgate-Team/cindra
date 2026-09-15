from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analytics import (
    daily_published_posts,
    generations_by_content_type,
    generations_overview,
    posts_by_content_kind,
    posts_by_platform,
    posts_overview,
)
from app.db import get_db
from app.deps import get_current_user
from app.models import GenerationContentType, UsageEventType, User
from app.schemas import (
    AnalyticsSummary,
    ContentKindCount,
    ContentTypeGenerationStats,
    DailyPostCount,
    PlatformPostStats,
    UsageLimitStat,
)
from app.usage import usage_summary

router = APIRouter(prefix="/analytics", tags=["analytics"])

_USAGE_LABELS: dict[tuple[UsageEventType, GenerationContentType | None], str] = {
    (UsageEventType.generation, GenerationContentType.text): "Текстовые генерации",
    (UsageEventType.generation, GenerationContentType.image): "Генерации изображений",
    (UsageEventType.generation, GenerationContentType.video): "Генерации видео",
    (UsageEventType.publication, None): "Публикации",
    (UsageEventType.long_video_generation, None): "Длинные AI-ролики",
    (UsageEventType.layout_render, None): "Карточки по шаблону",
}


@router.get("/summary", response_model=AnalyticsSummary)
def get_analytics_summary(
    # Bounded rather than open-ended -- an unbounded range would let a
    # request force an unbounded table scan/response size; a year is
    # generous for a personal content dashboard.
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyticsSummary:
    since = datetime.now(UTC) - timedelta(days=days)

    posts = posts_overview(db, current_user, since)
    generations = generations_overview(db, current_user, since)

    return AnalyticsSummary(
        period_days=days,
        posts_total=posts.total,
        posts_scheduled=posts.scheduled,
        posts_publishing=posts.publishing,
        posts_published=posts.published,
        posts_failed=posts.failed,
        publish_success_rate=posts.success_rate,
        posts_by_platform=[
            PlatformPostStats(platform=row.platform, published=row.published, failed=row.failed)
            for row in posts_by_platform(db, current_user, since)
        ],
        posts_by_content_kind=[
            ContentKindCount(content_kind=row.content_kind, count=row.count)
            for row in posts_by_content_kind(db, current_user, since)
        ],
        daily_published_posts=[
            DailyPostCount(day=row.day.isoformat(), published=row.published)
            for row in daily_published_posts(db, current_user, since)
        ],
        generations_total=generations.total,
        generations_queued=generations.queued,
        generations_processing=generations.processing,
        generations_completed=generations.completed,
        generations_failed=generations.failed,
        generations_flagged=generations.flagged,
        generation_success_rate=generations.success_rate,
        generations_by_content_type=[
            ContentTypeGenerationStats(
                content_type=row.content_type, completed=row.completed, failed=row.failed
            )
            for row in generations_by_content_type(db, current_user, since)
        ],
        usage_this_period=[
            UsageLimitStat(
                event_type=row.event_type,
                content_type=row.content_type,
                label=_USAGE_LABELS[(row.event_type, row.content_type)],
                used=row.used,
                limit=row.limit,
            )
            for row in usage_summary(db, current_user)
        ],
    )

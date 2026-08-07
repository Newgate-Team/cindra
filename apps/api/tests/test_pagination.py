from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GenerationContentType, GenerationJob, GenerationStatus, User
from app.pagination import paginate


def _job(user: User, topic: str) -> GenerationJob:
    return GenerationJob(
        user_id=user.id,
        content_type=GenerationContentType.text,
        status=GenerationStatus.completed,
        input_payload={"topic": topic},
        completed_at=datetime.now(UTC),
    )


def test_paginate_returns_total_and_limited_page(db: Session, user: User) -> None:
    for i in range(7):
        db.add(_job(user, f"тема {i}"))
    db.commit()

    query = select(GenerationJob).where(GenerationJob.user_id == user.id)
    rows, total = paginate(db, query, limit=3, offset=0)
    assert total == 7
    assert len(rows) == 3


def test_paginate_offset_moves_the_window(db: Session, user: User) -> None:
    for i in range(5):
        db.add(_job(user, f"тема {i}"))
    db.commit()

    query = select(GenerationJob).where(GenerationJob.user_id == user.id).order_by(GenerationJob.created_at)
    first_page, _ = paginate(db, query, limit=2, offset=0)
    second_page, _ = paginate(db, query, limit=2, offset=2)
    first_ids = {row[0].id for row in first_page}
    second_ids = {row[0].id for row in second_page}
    assert first_ids.isdisjoint(second_ids)


def test_paginate_offset_past_the_end_returns_empty(db: Session, user: User) -> None:
    db.add(_job(user, "тема"))
    db.commit()

    query = select(GenerationJob).where(GenerationJob.user_id == user.id)
    rows, total = paginate(db, query, limit=10, offset=50)
    assert total == 1
    assert rows == []

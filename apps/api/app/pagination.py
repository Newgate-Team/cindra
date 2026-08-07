from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import Row, Select, func, select
from sqlalchemy.orm import Session

# limit/offset, not cursor (CIN-108): nothing in this codebase has a
# high enough write rate for offset drift to matter, and limit/offset
# gives a free `total` for "page N of M" UI at a fraction of the code
# a cursor scheme needs -- worth revisiting only if a specific list
# (e.g. the feed, CIN-109) grows large enough for that tradeoff to flip.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


def paginate(db: Session, query: Select, limit: int, offset: int) -> tuple[Sequence[Row], int]:
    """Runs `query` with limit/offset applied plus a matching COUNT(*),
    returning raw Row objects (not unwrapped) -- works uniformly for
    both single-entity selects (`select(Model)`, unpack `for (row,) in
    rows`) and joined ones (`select(A, B)`, unpack `for a, b in rows`),
    since callers already know their own query's shape.
    """
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.limit(limit).offset(offset)).all()
    return rows, total

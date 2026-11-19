import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team, User


def visible_user_ids(db: Session, user: User) -> list[uuid.UUID]:
    """Whose resources `user` should see: just themselves if they're
    not on a team, everyone on the same team otherwise.

    This is the ONE place team membership turns into resource
    visibility -- every ownership-scoped query that gets taught about
    teams should filter on this list (e.g. `Post.user_id.in_(...)`)
    instead of re-deriving membership itself, so there's a single spot
    to get right and a single spot to test.
    """
    if user.team_id is None:
        return [user.id]
    return list(db.scalars(select(User.id).where(User.team_id == user.team_id)))


def billing_owner(db: Session, user: User) -> User:
    """Whose Subscription actually governs `user`'s tier/quota: the
    team owner's if `user` is on a team (a shared billing quota is the
    whole point of a team -- see app/usage.py), otherwise their own.

    The ONE place billing authority is resolved -- every place that
    looks up a Subscription to check a limit should resolve through
    this rather than always using `user`'s own row, or a team member
    on their own free-tier personal subscription would stay capped at
    it even while their team pays for Business.
    """
    if user.team_id is None:
        return user
    team = db.get(Team, user.team_id)
    if team is None:
        return user
    owner = db.get(User, team.owner_user_id)
    return owner if owner is not None else user

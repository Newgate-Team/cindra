from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Team, TeamInvite, User, UserRole
from app.scheduler.tasks import send_email_task
from app.schemas import (
    TeamCreate,
    TeamInviteAccept,
    TeamInviteCreate,
    TeamInviteOut,
    TeamOut,
)
from app.security import (
    TEAM_INVITE_TOKEN_EXPIRE_MINUTES,
    generate_url_token,
    hash_url_token,
)

router = APIRouter(prefix="/team", tags=["team"])


def _members(db: Session, team_id) -> list[User]:
    return list(db.scalars(select(User).where(User.team_id == team_id).order_by(User.created_at)))


def _to_out(team: Team, members: list[User]) -> TeamOut:
    return TeamOut(
        id=team.id,
        name=team.name,
        owner_user_id=team.owner_user_id,
        created_at=team.created_at,
        members=[
            {
                "id": member.id,
                "email": member.email,
                "role": member.role,
                "is_owner": member.id == team.owner_user_id,
            }
            for member in members
        ],
    )


def _get_own_team(db: Session, current_user: User) -> Team:
    if current_user.team_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Вы не состоите в команде"
        )
    team = db.get(Team, current_user.team_id)
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Вы не состоите в команде"
        )
    return team


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamOut:
    if current_user.team_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Вы уже состоите в команде"
        )
    if current_user.role != UserRole.agency:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Команды доступны только агентским аккаунтам",
        )

    team = Team(name=payload.name, owner_user_id=current_user.id)
    db.add(team)
    db.flush()
    current_user.team_id = team.id
    db.commit()
    db.refresh(team)
    return _to_out(team, [current_user])


@router.get("", response_model=TeamOut)
def get_team(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TeamOut:
    team = _get_own_team(db, current_user)
    return _to_out(team, _members(db, team.id))


@router.post("/invites", response_model=TeamInviteOut, status_code=status.HTTP_201_CREATED)
def create_team_invite(
    payload: TeamInviteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamInviteOut:
    team = _get_own_team(db, current_user)
    if team.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Приглашать участников может только владелец команды",
        )
    if not get_settings().smtp_host:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Отправка email не настроена на сервере",
        )

    existing_member = db.scalar(select(User).where(User.email == payload.email))
    if existing_member is not None and existing_member.team_id == team.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Этот email уже в команде"
        )

    now = datetime.now(UTC)
    # At most one valid invite per (team, email) at a time -- same
    # reasoning as password-reset: an older still-valid link shouldn't
    # remain a second, forgotten way in after a newer one is issued.
    for stale_invite in db.scalars(
        select(TeamInvite).where(
            TeamInvite.team_id == team.id,
            TeamInvite.email == payload.email,
            TeamInvite.accepted_at.is_(None),
            TeamInvite.expires_at > now,
        )
    ):
        stale_invite.accepted_at = now  # marks it dead, not literally "accepted"

    raw_token = generate_url_token()
    invite = TeamInvite(
        team_id=team.id,
        email=payload.email,
        invited_by_user_id=current_user.id,
        token_hash=hash_url_token(raw_token),
        expires_at=now + timedelta(minutes=TEAM_INVITE_TOKEN_EXPIRE_MINUTES),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    invite_link = f"{get_settings().frontend_base_url}/team/accept?token={raw_token}"
    days = TEAM_INVITE_TOKEN_EXPIRE_MINUTES // (60 * 24)
    send_email_task.delay(
        payload.email,
        f"Cindra — приглашение в команду «{team.name}»",
        f"Вас пригласили в команду «{team.name}» на Cindra. Чтобы присоединиться, "
        f"войдите (или зарегистрируйтесь с этим email) и перейдите по ссылке (действует "
        f"{days} дн.):\n\n{invite_link}",
    )
    return invite


@router.post("/invites/accept", response_model=TeamOut)
def accept_team_invite(
    payload: TeamInviteAccept,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamOut:
    token_hash = hash_url_token(payload.token)
    invite = db.scalar(select(TeamInvite).where(TeamInvite.token_hash == token_hash))
    now = datetime.now(UTC)
    if invite is None or invite.accepted_at is not None or invite.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Приглашение недействительно или устарело",
        )
    if invite.email != current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Это приглашение предназначено для другого email",
        )
    if current_user.team_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Вы уже состоите в команде — сначала покиньте текущую",
        )

    team = db.get(Team, invite.team_id)
    current_user.team_id = team.id
    invite.accepted_at = now
    db.commit()
    return _to_out(team, _members(db, team.id))


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_team_member(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    team = _get_own_team(db, current_user)
    if team.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Удалять участников может только владелец команды",
        )
    if str(team.owner_user_id) == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить владельца команды",
        )

    member = db.get(User, user_id)
    if member is None or member.team_id != team.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")

    member.team_id = None
    db.commit()

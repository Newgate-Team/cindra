"""Live avatar streams: the user sets one up and presses start; a
stream agent (stream_agent/, on a machine with ffmpeg) claims it, keeps
it on air, and asks /streams/agent/{id}/reply what the avatar should say.

Agent endpoints authenticate with the shared STREAM_AGENT_TOKEN. A
stream whose agent goes silent is handed to another agent -- for a
24/7 stream, a crashed machine must cost a minute, not the rest of the
day."""

import base64
import hmac
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import stream_avatars
from app.config import get_settings
from app.content_pipeline.errors import ContentModeratedError, TransientGenerationError
from app.content_pipeline.moderation import check_content
from app.content_pipeline.stream_replies import (
    ChatLine,
    ReplyGenerationError,
    generate_reply,
)
from app.content_pipeline.stream_speech import SAMPLE_RATE as SPEECH_SAMPLE_RATE
from app.content_pipeline.stream_speech import SpeechError, synthesize
from app.db import get_db
from app.deps import get_current_user
from app.integrations import find_integration, get_secret
from app.models import (
    LiveStream,
    StreamAgent,
    StreamEvent,
    StreamStatus,
    UsageEvent,
    UsageEventType,
    User,
)
from app.schemas import (
    LiveStreamCreate,
    LiveStreamOut,
    LiveStreamUpdate,
    StreamAgentEvent,
    StreamAgentFinish,
    StreamAgentHeartbeat,
    StreamAgentHeartbeatOut,
    StreamAgentHello,
    StreamAgentJobOut,
    StreamAgentsStatusOut,
    StreamEventOut,
    StreamProviderOut,
    StreamReplyOut,
    StreamReplyRequest,
    StreamVoiceOut,
)
from app.teams import visible_user_ids
from app.token_crypto import decrypt_token, encrypt_token
from app.usage import check_usage_limit

router = APIRouter(prefix="/streams", tags=["streams"])
log = logging.getLogger(__name__)

# The agent heartbeats every ~20s; this long without one means its
# machine died, and the stream goes to another agent.
_STALE_AFTER = timedelta(minutes=2)
_ONLINE_WINDOW = timedelta(minutes=2)
_HOUR = 3600


def _now() -> datetime:
    return datetime.now(UTC)


def _out(stream: LiveStream) -> LiveStreamOut:
    return LiveStreamOut.model_validate(stream)


def _visible(db: Session, stream_id: uuid.UUID, user: User) -> LiveStream:
    stream = db.get(LiveStream, stream_id)
    if stream is None or stream.user_id not in visible_user_ids(db, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Эфир не найден")
    return stream


def _check_voice(voice_id: str | None) -> None:
    if voice_id is not None and voice_id not in stream_avatars.TTS_VOICES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный голос")


def _event(db: Session, stream: LiveStream, kind: str, text: str, **fields: str | None) -> None:
    db.add(StreamEvent(stream_id=stream.id, kind=kind, text=text[:2000], **fields))


# ---- User side -----------------------------------------------------------


@router.post("", response_model=LiveStreamOut, status_code=status.HTTP_201_CREATED)
def create_stream(
    payload: LiveStreamCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveStreamOut:
    if payload.avatar_provider not in stream_avatars.PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный сервис аватара"
        )
    _check_voice(payload.avatar.voice_id)
    stream = LiveStream(
        user_id=current_user.id,
        title=payload.title,
        platform=payload.platform,
        channel=payload.channel,
        encrypted_stream_key=encrypt_token(payload.stream_key) if payload.stream_key else None,
        persona=payload.persona,
        idle_topics=payload.idle_topics,
        blocked_terms=payload.blocked_terms,
        avatar_provider=payload.avatar_provider,
        avatar=payload.avatar.model_dump(),
        min_reply_interval_seconds=payload.min_reply_interval_seconds,
        max_hours=payload.max_hours,
        stats={},
    )
    db.add(stream)
    db.commit()
    db.refresh(stream)
    return _out(stream)


@router.get("", response_model=list[LiveStreamOut])
def list_streams(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LiveStreamOut]:
    streams = db.scalars(
        select(LiveStream)
        .where(LiveStream.user_id.in_(visible_user_ids(db, current_user)))
        .order_by(LiveStream.created_at.desc())
    ).all()
    return [_out(s) for s in streams]


@router.get("/voices", response_model=list[StreamVoiceOut])
def stream_voices(current_user: User = Depends(get_current_user)) -> list[StreamVoiceOut]:
    return [
        StreamVoiceOut(id=voice, label=f"{voice} — {description}")
        for voice, description in stream_avatars.TTS_VOICES.items()
    ]


@router.get("/providers", response_model=list[StreamProviderOut])
def stream_providers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StreamProviderOut]:
    return [
        StreamProviderOut(
            id=key,
            label=provider.label,
            connected=find_integration(db, current_user, provider.integration) is not None,
        )
        for key, provider in stream_avatars.PROVIDERS.items()
    ]


@router.get("/agents/status", response_model=StreamAgentsStatusOut)
def agents_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamAgentsStatusOut:
    agents = db.scalar(
        select(func.count()).where(StreamAgent.last_seen_at > _now() - _ONLINE_WINDOW)
    )
    return StreamAgentsStatusOut(online=agents > 0, agents=agents)


@router.get("/{stream_id}", response_model=LiveStreamOut)
def get_stream(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveStreamOut:
    return _out(_visible(db, stream_id, current_user))


@router.patch("/{stream_id}", response_model=LiveStreamOut)
def update_stream(
    stream_id: uuid.UUID,
    payload: LiveStreamUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveStreamOut:
    """Editable on air too: persona, topics, banned words and pacing
    apply to the next reply (the API builds every reply from the row)."""
    stream = _visible(db, stream_id, current_user)
    changes = payload.model_dump(exclude_unset=True)
    on_air = stream.status in (StreamStatus.queued, StreamStatus.live)
    if on_air and changes.keys() & {"channel", "stream_key", "avatar"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Канал, ключ трансляции и аватар меняются только при остановленном эфире",
        )
    if "stream_key" in changes:
        key = changes.pop("stream_key")
        stream.encrypted_stream_key = encrypt_token(key) if key else None
    if "avatar" in changes:
        _check_voice(changes["avatar"].get("voice_id"))
        stream.avatar = changes.pop("avatar")
    for field, value in changes.items():
        if value is not None:
            setattr(stream, field, value)
    db.commit()
    db.refresh(stream)
    return _out(stream)


@router.delete("/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stream(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    stream = _visible(db, stream_id, current_user)
    if stream.status in (StreamStatus.queued, StreamStatus.live):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Сначала остановите эфир"
        )
    db.delete(stream)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{stream_id}/start", response_model=LiveStreamOut)
def start_stream(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveStreamOut:
    stream = _visible(db, stream_id, current_user)
    if stream.status in (StreamStatus.queued, StreamStatus.live):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Эфир уже запущен")
    if not stream.encrypted_stream_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Добавьте ключ трансляции Twitch (Панель автора → Настройки → Трансляция)",
        )
    owner = db.get(User, stream.user_id)
    provider = stream_avatars.PROVIDERS[stream.avatar_provider]
    if find_integration(db, owner, provider.integration) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Подключите {provider.label} на странице «Инструменты»",
        )
    # At least the first hour must fit -- later hours are checked as
    # they start (see agent_heartbeat).
    check_usage_limit(db, owner, UsageEventType.stream_hour)
    stream.status = StreamStatus.queued
    stream.stop_requested = False
    stream.error_message = None
    stream.agent_name = None
    stream.started_at = stream.ended_at = stream.heartbeat_at = None
    stream.hours_billed = 0
    stream.stats = {}
    _event(db, stream, "system", "Эфир поставлен в очередь")
    db.commit()
    db.refresh(stream)
    return _out(stream)


@router.post("/{stream_id}/stop", response_model=LiveStreamOut)
def stop_stream(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveStreamOut:
    stream = _visible(db, stream_id, current_user)
    if stream.status == StreamStatus.queued:
        stream.status = StreamStatus.ended
        stream.ended_at = _now()
        _event(db, stream, "system", "Эфир отменён до начала")
    elif stream.status == StreamStatus.live:
        # The agent sees this on its next heartbeat (seconds) and winds
        # the broadcast down cleanly.
        stream.stop_requested = True
        _event(db, stream, "system", "Остановка эфира запрошена")
    db.commit()
    db.refresh(stream)
    return _out(stream)


@router.get("/{stream_id}/events", response_model=list[StreamEventOut])
def stream_events(
    stream_id: uuid.UUID,
    after: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StreamEvent]:
    stream = _visible(db, stream_id, current_user)
    query = select(StreamEvent).where(StreamEvent.stream_id == stream.id)
    if after is not None:
        query = query.where(StreamEvent.created_at > after)
    return list(
        db.scalars(query.order_by(StreamEvent.created_at.desc()).limit(limit)).all()
    )


# ---- Agent side ----------------------------------------------------------


def require_stream_agent(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().stream_agent_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Агент эфиров не настроен на сервере (STREAM_AGENT_TOKEN)",
        )
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен агента")


def _touch_agent(db: Session, name: str, version: str | None = None) -> None:
    agent = db.get(StreamAgent, name)
    if agent is None:
        db.add(StreamAgent(name=name, version=version, last_seen_at=_now()))
    else:
        agent.last_seen_at = _now()
        if version:
            agent.version = version


def _agents_stream(db: Session, stream_id: uuid.UUID, agent_name: str) -> LiveStream:
    stream = db.get(LiveStream, stream_id, with_for_update=True, populate_existing=True)
    if stream is None or stream.status != StreamStatus.live or stream.agent_name != agent_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Эфир больше не закреплён за агентом"
        )
    return stream


@router.post("/agent/claim", response_model=StreamAgentJobOut)
def agent_claim(
    hello: StreamAgentHello,
    _: None = Depends(require_stream_agent),
    db: Session = Depends(get_db),
) -> StreamAgentJobOut | Response:
    _touch_agent(db, hello.agent_name, hello.version)
    while True:
        stream = db.scalar(
            select(LiveStream)
            .where(
                or_(
                    LiveStream.status == StreamStatus.queued,
                    and_(
                        LiveStream.status == StreamStatus.live,
                        LiveStream.heartbeat_at < _now() - _STALE_AFTER,
                    ),
                )
            )
            .order_by(LiveStream.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if stream is None:
            db.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        owner = db.get(User, stream.user_id)
        provider = stream_avatars.PROVIDERS.get(stream.avatar_provider)
        integration = (
            find_integration(db, owner, provider.integration) if provider and owner else None
        )
        if integration is None or not stream.encrypted_stream_key:
            # Disconnected after start was pressed: nothing to go live with.
            stream.status = StreamStatus.failed
            stream.error_message = "Сервис аватара отключён — подключите его и запустите эфир снова"
            stream.ended_at = _now()
            db.commit()
            continue
        taking_over = stream.status == StreamStatus.live
        stream.status = StreamStatus.live
        stream.agent_name = hello.agent_name
        stream.claimed_at = stream.heartbeat_at = _now()
        stream.started_at = stream.started_at or _now()
        _event(
            db,
            stream,
            "system",
            "Эфир подхвачен другим агентом после сбоя" if taking_over else "Эфир начинается",
        )
        db.commit()
        return StreamAgentJobOut(
            id=stream.id,
            title=stream.title,
            platform=stream.platform.value,
            channel=stream.channel,
            stream_key=decrypt_token(stream.encrypted_stream_key),
            avatar_provider=stream.avatar_provider,
            avatar=stream.avatar,
            avatar_api_key=get_secret(integration),
            min_reply_interval_seconds=stream.min_reply_interval_seconds,
            max_hours=stream.max_hours,
            started_at=stream.started_at,
        )


def _bill_started_hours(db: Session, stream: LiveStream, owner: User) -> str | None:
    """Counts each started hour on air against the plan as it begins
    (same team-pooled limit as everything else). Returns why the stream
    must stop now, if it must."""
    elapsed = (_now() - stream.started_at).total_seconds()
    if elapsed >= stream.max_hours * _HOUR:
        return f"Эфир шёл {stream.max_hours} ч — это заданная длительность"
    started_hours = int(elapsed // _HOUR) + 1
    while stream.hours_billed < started_hours:
        try:
            check_usage_limit(db, owner, UsageEventType.stream_hour, for_update=True)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_402_PAYMENT_REQUIRED:
                return "Закончились часы эфира на вашем тарифе в этом месяце"
            raise
        db.add(UsageEvent(user_id=owner.id, event_type=UsageEventType.stream_hour))
        db.flush()
        stream.hours_billed += 1
    return None


@router.post("/agent/{stream_id}/heartbeat", response_model=StreamAgentHeartbeatOut)
def agent_heartbeat(
    stream_id: uuid.UUID,
    beat: StreamAgentHeartbeat,
    _: None = Depends(require_stream_agent),
    db: Session = Depends(get_db),
) -> StreamAgentHeartbeatOut:
    stream = _agents_stream(db, stream_id, beat.agent_name)
    _touch_agent(db, beat.agent_name)
    stream.heartbeat_at = _now()
    stream.stats = {**stream.stats, **beat.stats}
    owner = db.get(User, stream.user_id)
    reason = "Эфир остановлен владельцем" if stream.stop_requested else None
    reason = reason or _bill_started_hours(db, stream, owner)
    db.commit()
    return StreamAgentHeartbeatOut(
        stop=reason is not None,
        stop_reason=reason,
        min_reply_interval_seconds=stream.min_reply_interval_seconds,
    )


def _recent_said(db: Session, stream: LiveStream) -> list[str]:
    return list(
        db.scalars(
            select(StreamEvent.text)
            .where(
                StreamEvent.stream_id == stream.id,
                StreamEvent.kind.in_(["reply", "idle", "notice"]),
            )
            .order_by(StreamEvent.created_at.desc())
            .limit(8)
        ).all()
    )


_FAILURE_LOG_EVERY = timedelta(minutes=5)


def _log_failure(db: Session, stream_id: uuid.UUID, agent_name: str, text: str) -> None:
    """Tell the owner why the avatar is silent -- once per few minutes,
    not on every retry (the agent asks again every few seconds)."""
    stream = _agents_stream(db, stream_id, agent_name)
    recent = db.scalar(
        select(StreamEvent.id).where(
            StreamEvent.stream_id == stream.id,
            StreamEvent.kind == "error",
            StreamEvent.text == text,
            StreamEvent.created_at > _now() - _FAILURE_LOG_EVERY,
        )
    )
    if recent is None:
        _event(db, stream, "error", text)
    db.commit()


@router.post("/agent/{stream_id}/reply", response_model=StreamReplyOut)
def agent_reply(
    stream_id: uuid.UUID,
    request: StreamReplyRequest,
    _: None = Depends(require_stream_agent),
    db: Session = Depends(get_db),
) -> StreamReplyOut:
    stream = _agents_stream(db, stream_id, request.agent_name)
    # Read what's needed, then release the row lock before the model call.
    persona, blocked = stream.persona, list(stream.blocked_terms or [])
    stream_avatar = dict(stream.avatar or {})
    topics = [t.strip() for t in stream.idle_topics.splitlines() if t.strip()]
    recent_said = _recent_said(db, stream)
    db.commit()

    target = request.target
    if target is not None:
        try:
            check_content(target.text, frozenset(blocked))
        except ContentModeratedError as exc:
            stream = _agents_stream(db, stream_id, request.agent_name)
            _event(db, stream, "blocked", f"Сообщение пропущено: {exc}", author=target.author,
                   message=target.text[:600])
            db.commit()
            return StreamReplyOut(say=None, skipped=str(exc))

    try:
        reply = generate_reply(
            persona=persona,
            mode=request.mode,
            blocked_terms=blocked,
            target=ChatLine(target.author, target.text) if target else None,
            recent_chat=[ChatLine(c.author, c.text) for c in request.recent_chat],
            recent_said=recent_said,
            topic=random.choice(topics) if topics else None,
            notice=request.notice.model_dump() if request.notice else None,
        )
    except ContentModeratedError as exc:
        stream = _agents_stream(db, stream_id, request.agent_name)
        _event(db, stream, "blocked", f"Ответ не прозвучал: {exc}",
               author=target.author if target else None,
               message=target.text[:600] if target else None)
        db.commit()
        return StreamReplyOut(say=None, skipped=str(exc))
    except TransientGenerationError as exc:
        _log_failure(db, stream_id, request.agent_name,
                     "Ответы временно недоступны — сервис генерации не отвечает")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ReplyGenerationError as exc:
        log.warning("stream reply failed: %s", exc)
        _log_failure(db, stream_id, request.agent_name,
                     "Аватар молчит: не удалось сгенерировать ответ — проверьте настройки сервера")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    voice = (stream_avatar.get("voice_id") or stream_avatars.DEFAULT_TTS_VOICE)
    try:
        audio = synthesize(reply.say, voice=voice, emotion=reply.emotion)
    except (TransientGenerationError, SpeechError) as exc:
        log.warning("stream speech failed: %s", exc)
        _log_failure(db, stream_id, request.agent_name,
                     "Аватар молчит: не удалось озвучить ответ — проверьте настройки сервера")
        return StreamReplyOut(say=None, skipped="Не удалось озвучить ответ")

    stream = _agents_stream(db, stream_id, request.agent_name)
    kind = {"answer": "reply", "idle": "idle", "notice": "notice"}[request.mode]
    author = target.author if target else (request.notice.author if request.notice else None)
    _event(
        db,
        stream,
        kind,
        reply.say,
        author=author,
        message=(target.text if target else request.notice.text if request.notice else None),
        emotion=reply.emotion,
    )
    db.commit()
    return StreamReplyOut(
        say=reply.say,
        emotion=reply.emotion,
        reaction=reply.reaction,
        audio_b64=base64.b64encode(audio).decode("ascii"),
        sample_rate=SPEECH_SAMPLE_RATE,
    )


@router.post("/agent/{stream_id}/event", status_code=status.HTTP_204_NO_CONTENT)
def agent_event(
    stream_id: uuid.UUID,
    event: StreamAgentEvent,
    _: None = Depends(require_stream_agent),
    db: Session = Depends(get_db),
) -> Response:
    stream = _agents_stream(db, stream_id, event.agent_name)
    _event(db, stream, event.kind, event.text)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agent/{stream_id}/finish", status_code=status.HTTP_204_NO_CONTENT)
def agent_finish(
    stream_id: uuid.UUID,
    finish: StreamAgentFinish,
    _: None = Depends(require_stream_agent),
    db: Session = Depends(get_db),
) -> Response:
    stream = _agents_stream(db, stream_id, finish.agent_name)
    stream.status = StreamStatus.failed if finish.error else StreamStatus.ended
    stream.error_message = finish.error
    stream.ended_at = _now()
    stream.stop_requested = False
    _event(db, stream, "error" if finish.error else "system",
           finish.error or "Эфир завершён")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

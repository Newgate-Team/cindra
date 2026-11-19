"""Google Drive: connect (OAuth), the auto-save switch, and saving a
file by hand. The copying itself is app/drive_exports.py."""

import logging
import uuid
from collections.abc import Callable

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.drive_exports import FOLDER_NAME, kind_label, queue_export, zone
from app.integrations import (
    find_integration,
    get_secret,
    integration_out,
    upsert_integration,
)
from app.models import (
    DriveExport,
    DriveExportStatus,
    Integration,
    IntegrationProvider,
    User,
)
from app.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas import (
    DriveExportCreate,
    DriveExportOut,
    GoogleDriveConnectRequest,
    GoogleDriveSettingsIn,
    GoogleDriveStartOut,
    IntegrationOut,
)
from app.security import (
    create_google_drive_oauth_state,
    decode_google_drive_oauth_state,
)
from app.social_integrations.media_validation import is_own_media_url
from app.tool_integrations import google_drive

router = APIRouter(prefix="/integrations/google-drive", tags=["integrations"])
log = logging.getLogger(__name__)

# Manual saves only queue work for our own worker, but each one is a
# download + upload of up to hundreds of MB -- a ceiling on what one
# user can have in flight at once.
_MAX_IN_FLIGHT = 20


def _drive_call[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except google_drive.DriveTransientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except google_drive.DriveError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _own_drive(db: Session, user: User) -> Integration:
    integration = find_integration(db, user, IntegrationProvider.google_drive)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Google Drive не подключён"
        )
    if integration.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Это Google Drive коллеги — менять его настройки может только он",
        )
    return integration


@router.post("/start", response_model=GoogleDriveStartOut)
def start_google_drive_oauth(
    current_user: User = Depends(get_current_user),
) -> GoogleDriveStartOut:
    settings = get_settings()
    if not settings.google_drive_client_id or not settings.google_drive_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Drive ещё не настроен на сервере",
        )
    state = create_google_drive_oauth_state(current_user.id)
    return GoogleDriveStartOut(authorization_url=google_drive.authorization_url(state))


@router.post("/connect", response_model=IntegrationOut)
def connect_google_drive(
    payload: GoogleDriveConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    try:
        state_user_id = decode_google_drive_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подключение Google Drive устарело — начните заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подключение Google Drive было начато другим пользователем",
        )

    token = _drive_call(lambda: google_drive.exchange_code(payload.code))
    if google_drive.SCOPE not in str(token.get("scope", "")).split():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Без разрешения создавать файлы в Google Drive Cindra не сможет "
            "сохранять туда контент — подключите заново и оставьте галочку",
        )
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google не выдал постоянный доступ — подключите Google Drive заново",
        )
    access_token = token["access_token"]
    label = google_drive.account_label(
        _drive_call(lambda: google_drive.get_account(access_token))
    )

    # Reconnecting the same Google account keeps its folder and the
    # auto-save choice; a different account gets its own new folder.
    own = db.scalar(
        select(Integration).where(
            Integration.user_id == current_user.id,
            Integration.provider == IntegrationProvider.google_drive,
        )
    )
    previous = own.settings if own is not None and own.account_label == label else {}
    folder_id = previous.get("folder_id")
    if folder_id and _drive_call(lambda: google_drive.folder_usable(access_token, folder_id)):
        folder = {"id": folder_id, "webViewLink": previous.get("folder_url")}
    else:
        folder = _drive_call(lambda: google_drive.create_folder(access_token, FOLDER_NAME))

    integration = upsert_integration(
        db,
        current_user,
        IntegrationProvider.google_drive,
        refresh_token,
        label,
        settings={
            "folder_id": folder["id"],
            "folder_url": folder.get("webViewLink"),
            "auto_save": previous.get("auto_save", True),
            "timezone": payload.timezone if zone(payload.timezone) else previous.get("timezone"),
        },
    )
    return integration_out(integration, current_user)


@router.patch("", response_model=IntegrationOut)
def update_google_drive(
    payload: GoogleDriveSettingsIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    integration = _own_drive(db, current_user)
    integration.settings = {**integration.settings, "auto_save": payload.auto_save}
    db.commit()
    db.refresh(integration)
    return integration_out(integration, current_user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_google_drive(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    integration = _own_drive(db, current_user)
    try:
        # Best effort: also withdraw the grant on Google's side, so
        # "disconnect" means Cindra really can't write there any more.
        google_drive.revoke(get_secret(integration))
    except google_drive.DriveError as exc:
        log.warning("google drive revoke failed: %s", exc)
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/exports", response_model=DriveExportOut, status_code=status.HTTP_201_CREATED)
def save_to_drive(
    payload: DriveExportCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DriveExport:
    if not is_own_media_url(payload.url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="В Google Drive можно сохранить только файлы из Cindra",
        )
    integration = find_integration(db, current_user, IntegrationProvider.google_drive)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Google Drive не подключён"
        )
    # Already there (typically auto-saved when it was generated): point
    # at that copy instead of putting a second one in the folder.
    saved = db.scalar(
        select(DriveExport)
        .where(
            DriveExport.integration_id == integration.id,
            DriveExport.source_url == payload.url,
            DriveExport.status == DriveExportStatus.completed,
        )
        .order_by(DriveExport.created_at.desc())
        .limit(1)
    )
    if saved is not None:
        return saved
    in_flight = db.scalar(
        select(func.count()).where(
            DriveExport.user_id == current_user.id,
            DriveExport.status.in_([DriveExportStatus.queued, DriveExportStatus.processing]),
        )
    )
    if in_flight >= _MAX_IN_FLIGHT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="В очереди на Google Drive уже много файлов — дождитесь, пока они сохранятся",
        )
    export = queue_export(
        db,
        current_user,
        url=payload.url,
        label=kind_label(payload.url),
        title=payload.title,
        origin="manual",
    )
    if export is None:  # disconnected between the check and here
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Google Drive не подключён"
        )
    db.refresh(export)
    return export


@router.get("/exports", response_model=Page[DriveExportOut])
def list_drive_exports(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    # The save button asks "is this file already in my Drive?"
    url: str | None = Query(default=None, max_length=1024),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[DriveExportOut]:
    query = (
        select(DriveExport)
        .where(DriveExport.user_id == current_user.id)
        .order_by(DriveExport.created_at.desc(), DriveExport.id)
    )
    if url is not None:
        query = query.where(DriveExport.source_url == url)
    rows, total = paginate(db, query, limit, offset)
    return Page[DriveExportOut](
        items=[DriveExportOut.model_validate(row) for (row,) in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/exports/{export_id}", response_model=DriveExportOut)
def get_drive_export(
    export_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DriveExport:
    export = db.get(DriveExport, export_id)
    if export is None or export.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
    return export

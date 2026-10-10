from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Integration, IntegrationProvider, User
from app.teams import visible_user_ids
from app.token_crypto import decrypt_token, encrypt_token


def find_integration(
    db: Session, user: User, provider: IntegrationProvider
) -> Integration | None:
    """The user's own connection first, otherwise a teammate's -- same
    team-shared visibility as connected social accounts, so an agency
    owner connects HeyGen once and the whole team generates with it."""
    rows = db.scalars(
        select(Integration).where(
            Integration.provider == provider,
            Integration.user_id.in_(visible_user_ids(db, user)),
        )
    ).all()
    own = next((row for row in rows if row.user_id == user.id), None)
    return own or (rows[0] if rows else None)


def get_secret(integration: Integration) -> str:
    return decrypt_token(integration.encrypted_secret)


def upsert_integration(
    db: Session,
    user: User,
    provider: IntegrationProvider,
    secret: str,
    account_label: str | None,
) -> Integration:
    """Always writes the *caller's own* row, even when a teammate's
    connection is already visible -- connecting your own key is an
    explicit choice to use your account instead of theirs."""

    def _own() -> Integration | None:
        return db.scalar(
            select(Integration).where(
                Integration.user_id == user.id, Integration.provider == provider
            )
        )

    def _apply(row: Integration) -> None:
        row.encrypted_secret = encrypt_token(secret)
        row.account_label = account_label
        row.updated_at = datetime.now(UTC)

    integration = _own()
    if integration is None:
        integration = Integration(user_id=user.id, provider=provider)
        _apply(integration)
        db.add(integration)
        try:
            db.commit()
        except IntegrityError:
            # Double-submitted connect form -- both requests carry the
            # same freshly-validated key, so updating whichever row won
            # is the right outcome, not an error (same resolution as
            # upsert_social_account's connect race).
            db.rollback()
            integration = _own()
            _apply(integration)
            db.commit()
    else:
        _apply(integration)
        db.commit()
    db.refresh(integration)
    return integration

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SocialAccount, SocialPlatform, User
from app.token_crypto import decrypt_token, encrypt_token


def _apply_account_fields(
    account: SocialAccount,
    access_token: str,
    refresh_token: str | None,
    token_expires_at: datetime | None,
    display_name: str | None,
) -> None:
    account.display_name = display_name
    account.encrypted_access_token = encrypt_token(access_token)
    account.encrypted_refresh_token = (
        encrypt_token(refresh_token) if refresh_token is not None else None
    )
    account.token_expires_at = token_expires_at


def upsert_social_account(
    db: Session,
    user: User,
    platform: SocialPlatform,
    external_account_id: str,
    access_token: str,
    refresh_token: str | None = None,
    token_expires_at: datetime | None = None,
    display_name: str | None = None,
) -> SocialAccount:
    """Store (or refresh) a connected social account's tokens, encrypted at rest.

    Called by each platform's OAuth callback (CIN-5/CIN-6) once it has
    exchanged an auth code for tokens -- this module only owns storage.
    """
    account = db.scalar(
        select(SocialAccount).where(
            SocialAccount.user_id == user.id,
            SocialAccount.platform == platform,
            SocialAccount.external_account_id == external_account_id,
        )
    )
    is_new = account is None
    if is_new:
        account = SocialAccount(
            user_id=user.id,
            platform=platform,
            external_account_id=external_account_id,
        )
        db.add(account)

    _apply_account_fields(account, access_token, refresh_token, token_expires_at, display_name)

    if is_new:
        try:
            db.commit()
        except IntegrityError:
            # Same class of gap CIN-158/162 and the auth.py register/
            # google races closed: two concurrent OAuth callbacks for
            # the same (user, platform, external_account_id) -- a
            # double-clicked "Connect" or a duplicate callback tab --
            # can both pass the check above and both insert. The
            # `uq_social_account` constraint catches the second one;
            # without this, that race surfaced as an unhandled 500
            # instead of just updating the row the other request
            # already created (both calls carry a freshly-exchanged
            # token for the same real account, so "update whichever
            # commits last" is the correct resolution, not an error).
            db.rollback()
            account = db.scalar(
                select(SocialAccount).where(
                    SocialAccount.user_id == user.id,
                    SocialAccount.platform == platform,
                    SocialAccount.external_account_id == external_account_id,
                )
            )
            _apply_account_fields(
                account, access_token, refresh_token, token_expires_at, display_name
            )
            db.commit()
    else:
        db.commit()

    db.refresh(account)
    return account


def get_access_token(account: SocialAccount) -> str:
    return decrypt_token(account.encrypted_access_token)


def get_refresh_token(account: SocialAccount) -> str | None:
    if account.encrypted_refresh_token is None:
        return None
    return decrypt_token(account.encrypted_refresh_token)

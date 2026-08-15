from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SocialAccount, SocialPlatform, User
from app.token_crypto import decrypt_token, encrypt_token


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
    if account is None:
        account = SocialAccount(
            user_id=user.id,
            platform=platform,
            external_account_id=external_account_id,
        )
        db.add(account)

    account.display_name = display_name
    account.encrypted_access_token = encrypt_token(access_token)
    account.encrypted_refresh_token = (
        encrypt_token(refresh_token) if refresh_token is not None else None
    )
    account.token_expires_at = token_expires_at

    db.commit()
    db.refresh(account)
    return account


def get_access_token(account: SocialAccount) -> str:
    return decrypt_token(account.encrypted_access_token)


def get_refresh_token(account: SocialAccount) -> str | None:
    if account.encrypted_refresh_token is None:
        return None
    return decrypt_token(account.encrypted_refresh_token)

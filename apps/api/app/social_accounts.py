from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SocialAccount, SocialPlatform, Subscription, User
from app.plans import PLAN_LIMITS, effective_tier
from app.teams import visible_user_ids
from app.token_crypto import decrypt_token, encrypt_token


def _enforce_connected_account_limit(db: Session, user: User) -> None:
    """CIN-155: only free tier actually limits this (1 account; Pro/
    Business are unlimited) -- see plans.py.

    Roadmap item 5: counted across the whole team (visible_user_ids),
    not just this user -- a shared team quota is the entire point of
    sharing accounts in the first place, and counting only the calling
    user's own rows here would let each teammate independently connect
    up to the free-tier limit, multiplying it by team size. The limit
    itself still resolves off `user`'s own Subscription (billing quota
    pooling -- whose subscription actually governs a team -- is its
    own separate, not-yet-wired concern; this only fixes the counting
    side, which is a real bypass if left alone once accounts are
    shared).

    Facebook rows are excluded from both the count and the check
    itself: connect_instagram() always creates one alongside the
    Instagram row in the same OAuth grant (CIN-65, no separate consent
    screen), so from the user's own perspective "Connect Instagram" is
    one action, not two platforms they chose to add. There's no
    standalone "connect Facebook" flow -- a Facebook row never exists
    without a paired Instagram row -- so this never lets a real extra
    connection through uncounted.

    Only checked for genuinely NEW rows (see upsert_social_account) --
    refreshing an already-connected account's token never counts
    against the limit, and existing over-limit accounts from before
    this check existed are never revoked, only blocked from adding
    more.

    Plain check-then-insert, not locked like the CIN-158 usage-limit
    checks: this guards a standing count, not a per-period spend, and
    the worst case of losing the race (one extra free-tier connection
    slot) isn't worth the added complexity for what's already a rare,
    low-value thing to even try to race.
    """
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    tier = effective_tier(subscription)
    limit = PLAN_LIMITS[tier].max_connected_accounts
    if limit is None:
        return
    current = db.scalar(
        select(func.count())
        .select_from(SocialAccount)
        .where(
            SocialAccount.user_id.in_(visible_user_ids(db, user)),
            SocialAccount.platform != SocialPlatform.facebook,
        )
    )
    if current >= limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Лимит тарифа исчерпан: {limit} подключённых аккаунтов "
                f"на тарифе {tier.value}"
            ),
        )


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

    The existing-account lookup is team-wide (roadmap item 5): if a
    teammate already connected this exact channel, reconnecting it
    refreshes that same shared row instead of creating a second one
    for the whole team to see. `account.user_id` itself doesn't move
    to the new connector -- it stays "who originally connected this",
    consistent with SocialAccount's own row never changing ownership
    elsewhere either.
    """
    account = db.scalar(
        select(SocialAccount).where(
            SocialAccount.user_id.in_(visible_user_ids(db, user)),
            SocialAccount.platform == platform,
            SocialAccount.external_account_id == external_account_id,
        )
    )
    is_new = account is None
    if is_new:
        if platform != SocialPlatform.facebook:
            _enforce_connected_account_limit(db, user)
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
            #
            # `uq_social_account` is scoped to (user_id, platform,
            # external_account_id), not team-wide -- two DIFFERENT
            # teammates connecting the exact same channel within the
            # same race window is a real but much rarer edge case this
            # doesn't cover (each insert has a different user_id, so
            # neither violates the constraint; you'd get two rows for
            # one channel instead of one). Accepted the same way
            # _enforce_connected_account_limit's own docstring accepts
            # its race: not worth a schema change for this unlikely a
            # collision on an already lowest-priority feature.
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

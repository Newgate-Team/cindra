class TransientPublishError(Exception):
    """Retryable failure publishing to a social platform (rate limit, timeout, 5xx)."""


class PermanentPublishError(Exception):
    """Non-retryable failure (bad token, revoked permission, unknown chat/account)."""

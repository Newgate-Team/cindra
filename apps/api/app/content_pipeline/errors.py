class TransientGenerationError(Exception):
    """Raised by a generator for retryable failures (rate limit, timeout, 5xx).

    Anything else raised by a generator is treated as permanent and
    fails the job immediately without retrying.
    """


class ContentModeratedError(Exception):
    """Raised by a generator (or moderation step) when output is rejected."""

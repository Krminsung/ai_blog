"""Pure retry-delay calculations shared by backend services and workers."""

from __future__ import annotations

import hashlib


def capped_exponential_delay(
    *,
    base_seconds: int,
    maximum_seconds: int,
    exponent: int,
) -> int:
    """Calculate exponential backoff capped at a policy maximum."""

    return min(maximum_seconds, base_seconds * (2 ** max(0, exponent)))


def deterministic_jittered_delay(
    *,
    base_seconds: int,
    maximum_seconds: int,
    jitter_ratio: float,
    attempt_no: int,
    seed: str,
) -> int:
    """Apply reproducible symmetric jitter to an attempt-based backoff."""

    delay = capped_exponential_delay(
        base_seconds=base_seconds,
        maximum_seconds=maximum_seconds,
        exponent=attempt_no - 1,
    )
    digest = int(hashlib.sha256(f"{seed}:{attempt_no}".encode()).hexdigest()[:8], 16)
    unit = digest / 0xFFFFFFFF
    return max(1, round(delay * (1 - jitter_ratio + 2 * jitter_ratio * unit)))

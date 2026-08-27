"""Deterministic serialization helpers shared across backend domains."""

from __future__ import annotations

import hashlib
import json


def canonical_json_hash(value: object) -> str:
    """Return the SHA-256 digest of the application's canonical JSON encoding."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

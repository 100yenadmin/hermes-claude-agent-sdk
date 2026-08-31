"""Dependency-free parity primitives.

The canonical JSON and closed trace registry are safe to import offline. The
catalog and result packet schemas remain behind their reviewed design gate and
are intentionally not imported here until that contract is finalized.
"""

from .canonical import (
    CanonicalizationError,
    SDK_EVENT_CODES,
    TRACE_REGISTRY,
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    canonicalize,
    load_json,
    stable_json_hash,
    validate_identifier,
    validate_sha256,
)

__all__ = [
    "CanonicalizationError",
    "SDK_EVENT_CODES",
    "TRACE_REGISTRY",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_sha256",
    "canonicalize",
    "load_json",
    "stable_json_hash",
    "validate_identifier",
    "validate_sha256",
]

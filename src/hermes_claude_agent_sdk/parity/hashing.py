"""Canonical hashing helpers for versioned parity artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON for a JSON-compatible value."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    """Hash a JSON-compatible value after canonical serialization."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash a regular file without interpreting its content."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_compatible(value: Any) -> Any:
    """Copy supported values into deterministic JSON-compatible containers."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError("non-finite numbers are not supported")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            result[key] = json_compatible(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_compatible(item) for item in value]
    raise TypeError(f"unsupported value type: {type(value).__name__}")


__all__ = ["canonical_json_bytes", "json_compatible", "sha256_file", "sha256_value"]

"""Sanitized isolated-profile manifests for candidate binding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import json_compatible, sha256_value


class ProfileViolation(ValueError):
    """The requested profile is ambiguous, shared, or not hash-bound."""


_FIELDS = frozenset(
    {
        "schema_version",
        "profile_id",
        "isolation_kind",
        "persistent",
        "shared_state",
        "customer_data",
        "configuration_hash",
    }
)
_MAX_PROFILE_BYTES = 64 * 1024


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProfileViolation(f"{field} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ProfileManifest:
    profile_id: str
    isolation_kind: str
    persistent: bool
    configuration_hash: str
    manifest_hash: str


def load_profile_manifest(
    path: str | Path,
    *,
    expected_profile: str | None = None,
) -> ProfileManifest:
    profile_path = Path(path).expanduser().resolve()
    if not profile_path.is_file() or profile_path.stat().st_size > _MAX_PROFILE_BYTES:
        raise ProfileViolation("profile manifest is missing or exceeds the bounded file size")
    try:
        root = json_compatible(json.loads(profile_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProfileViolation(f"profile manifest cannot be parsed safely: {exc}") from exc
    if not isinstance(root, dict) or set(root) != _FIELDS:
        raise ProfileViolation("profile manifest fields do not match schema")
    if root["schema_version"] != 1:
        raise ProfileViolation("profile manifest schema_version must equal 1")
    profile_id = root["profile_id"]
    if not isinstance(profile_id, str) or not profile_id:
        raise ProfileViolation("profile_id must be a non-empty string")
    if expected_profile is not None and profile_id != expected_profile:
        raise ProfileViolation("profile manifest does not match the requested profile")
    isolation_kind = root["isolation_kind"]
    if isolation_kind not in {"in_process_fixture", "local_profile"}:
        raise ProfileViolation("isolation_kind is unsupported")
    if type(root["persistent"]) is not bool:
        raise ProfileViolation("persistent must be a boolean")
    if isolation_kind == "in_process_fixture" and root["persistent"] is not False:
        raise ProfileViolation("in-process fixtures cannot claim persistent profile state")
    if isolation_kind == "local_profile" and root["persistent"] is not True:
        raise ProfileViolation("local profiles must declare persistent profile state")
    if root["shared_state"] is not False or root["customer_data"] is not False:
        raise ProfileViolation("shared state and customer data are forbidden")
    configuration_hash = _sha256(root["configuration_hash"], "configuration_hash")
    return ProfileManifest(
        profile_id=profile_id,
        isolation_kind=isolation_kind,
        persistent=root["persistent"],
        configuration_hash=configuration_hash,
        manifest_hash=sha256_value(root),
    )


__all__ = ["ProfileManifest", "ProfileViolation", "load_profile_manifest"]

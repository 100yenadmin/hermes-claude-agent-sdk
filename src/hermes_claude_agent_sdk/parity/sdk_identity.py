"""Fail-closed SDK identity binding for executable parity candidates."""

from __future__ import annotations

import re
from importlib import metadata

from ..compatibility import SDK_DISTRIBUTION, SDK_MAX_VERSION, SDK_MIN_VERSION

_SDK_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


class SDKIdentityViolation(ValueError):
    """An executable parity candidate is not bound to its installed SDK."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _version_tuple(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _SDK_VERSION_RE.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def resolve_candidate_sdk_version(candidate_version: object | None) -> str:
    """Return the exact supported installed SDK version or fail closed.

    ``None`` means the caller omitted an SDK version, so the installed
    distribution becomes the candidate identity. An explicit value must be an
    exact match. Metadata inspection never imports ``claude_agent_sdk``.
    """

    try:
        installed_version = metadata.version(SDK_DISTRIBUTION)
    except metadata.PackageNotFoundError as exc:
        raise SDKIdentityViolation("sdk_distribution_unavailable") from exc
    except Exception as exc:
        raise SDKIdentityViolation("sdk_distribution_unavailable") from exc

    installed = _version_tuple(installed_version)
    minimum = _version_tuple(SDK_MIN_VERSION)
    maximum = _version_tuple(SDK_MAX_VERSION)
    if installed is None or minimum is None or maximum is None:
        raise SDKIdentityViolation("sdk_version_malformed")
    if candidate_version is None:
        if not minimum <= installed < maximum:
            raise SDKIdentityViolation("sdk_version_unsupported")
        return installed_version

    candidate = _version_tuple(candidate_version)
    if candidate is None:
        raise SDKIdentityViolation("sdk_version_malformed")
    if candidate_version != installed_version:
        raise SDKIdentityViolation("sdk_version_mismatch")
    if not minimum <= installed < maximum:
        raise SDKIdentityViolation("sdk_version_unsupported")
    return installed_version


def candidate_sdk_failure(candidate_version: object) -> str | None:
    """Return a bounded preflight reason instead of raising."""

    try:
        resolve_candidate_sdk_version(candidate_version)
    except SDKIdentityViolation as exc:
        return exc.reason_code
    return None


__all__ = [
    "SDKIdentityViolation",
    "candidate_sdk_failure",
    "resolve_candidate_sdk_version",
]

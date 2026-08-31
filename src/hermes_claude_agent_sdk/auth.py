"""Fail-closed Claude CLI subscription authentication preflight.

The Claude Agent SDK uses the local Claude CLI subscription session rather
than an API key.  This module provides the small, dependency-free gate that
must run before importing the SDK, constructing a client, or issuing a query.

Only the four documented status fields are inspected.  The status payload is
never retained in a result, and the result contains only a boolean and one
stable category.  Unknown or incomplete status is rejected.
"""

from __future__ import annotations

import json
import math
import os
import select
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AuthCategory(str, Enum):
    """Sanitized outcome categories returned by the auth preflight."""

    SUBSCRIPTION_OAUTH = "subscription_oauth"
    LOGGED_OUT = "logged_out"
    API_KEY = "api_key"
    METERED = "metered"
    THIRD_PARTY = "third_party"
    MISSING_SUBSCRIPTION = "missing_subscription"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    TIMEOUT = "timeout"
    CLI_MISSING = "cli_missing"
    NONZERO = "nonzero"
    PROBE_FAILED = "probe_failed"
    UNKNOWN = "unknown"


# ``AuthPreflightReason`` is a readable alias for callers that prefer a
# reason-oriented name.  Both names identify the same stable enum.
AuthPreflightReason = AuthCategory


@dataclass(frozen=True, slots=True)
class AuthPreflightResult:
    """The complete public result of parsing or probing Claude auth status.

    Deliberately do not add a payload, error, command, or exception field:
    those values can contain identity or credential material.  ``reason`` and
    ``status`` are compatibility views over the same enum, not extra data.
    """

    allowed: bool
    category: AuthCategory

    @property
    def is_allowed(self) -> bool:
        """Boolean compatibility view of :attr:`allowed`."""

        return self.allowed

    @property
    def reason(self) -> AuthCategory:
        """Stable category compatibility view."""

        return self.category

    @property
    def status(self) -> AuthCategory:
        """Stable category compatibility view."""

        return self.category


ALLOWED_AUTH_CATEGORY = AuthCategory.SUBSCRIPTION_OAUTH


@dataclass(frozen=True, slots=True)
class _BoundedCompleted:
    returncode: int
    stdout: bytes
    oversized: bool = False

_DEFAULT_MAX_STATUS_BYTES = 64 * 1024
_MAX_STATUS_BYTES = 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 30.0
_MIN_TIMEOUT_SECONDS = 0.05
_MAX_ENUM_TEXT_LENGTH = 128
_MAX_MAPPING_KEYS = 128
_CLAUDE_AUTH_ARGV = ("claude", "auth", "status", "--json")
_STATUS_FIELDS = frozenset(
    {"loggedIn", "authMethod", "apiProvider", "subscriptionType"}
)

# The CLI reads the current user's local session from HOME.  Keep the child
# environment explicit and small so API-key/token variables cannot select a
# metered route.  ``CLAUDE_CONFIG_DIR`` is non-secret and allows an operator's
# normal CLI config location to remain effective when one is explicitly set.
_PROBE_ENV_KEYS = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "XDG_CONFIG_HOME",
    "CLAUDE_CONFIG_DIR",
)

_OAUTH_METHODS = frozenset(
    {
        "oauth",
        "oauth_token",
        "claude.ai",
        "claude_ai",
        "first_party_oauth",
        "first-party-oauth",
    }
)
_API_KEY_METHODS = frozenset(
    {"api_key", "api-key", "apikey", "api key", "key", "api"}
)
_METERED_METHODS = frozenset(
    {
        "metered",
        "payg",
        "pay_as_you_go",
        "pay-as-you-go",
        "usage_based",
        "usage-based",
        "extra_usage",
        "extra-usage",
    }
)
_FIRST_PARTY_PROVIDERS = frozenset(
    {
        "firstparty",
        "first_party",
        "first-party",
        "anthropic",
        "claude.ai",
        "claude_ai",
    }
)
_THIRD_PARTY_PROVIDERS = frozenset(
    {
        "thirdparty",
        "third_party",
        "third-party",
        "bedrock",
        "aws_bedrock",
        "aws-bedrock",
        "vertex",
        "google_vertex",
        "google-vertex",
        "foundry",
        "azure",
    }
)
# These are subscription labels, not every string the CLI might emit.  In
# particular, ``free``, ``unknown``, and usage-based labels are not allowed.
_SUBSCRIPTION_TYPES = frozenset(
    {
        "pro",
        "max",
        "max_5x",
        "max-5x",
        "max_20x",
        "max-20x",
        "team",
        "business",
        "enterprise",
        "education",
        "edu",
    }
)
_METERED_SUBSCRIPTION_TYPES = frozenset(
    {
        "metered",
        "payg",
        "pay_as_you_go",
        "pay-as-you-go",
        "usage_based",
        "usage-based",
        "extra_usage",
        "extra-usage",
    }
)


def _result(category: AuthCategory) -> AuthPreflightResult:
    return AuthPreflightResult(
        allowed=category is AuthCategory.SUBSCRIPTION_OAUTH,
        category=category,
    )


def _normalise_enum_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    if not value or len(value) > _MAX_ENUM_TEXT_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


def _normalise_status_mapping(payload: object) -> Mapping[str, object] | None:
    """Return only the four decision fields from a bounded mapping."""

    if not isinstance(payload, Mapping):
        return None
    try:
        size = len(payload)
    except Exception:
        return None
    if size > _MAX_MAPPING_KEYS:
        return None
    result: dict[str, object] = {}
    try:
        for key in _STATUS_FIELDS:
            if key in payload:
                result[key] = payload[key]
    except Exception:
        return None
    return result


def _mapping_is_oversized(
    payload: Mapping[str, object],
    *,
    max_status_bytes: int,
) -> bool:
    """Bound direct-mapping decision fields without serializing them."""

    # This is a conservative JSON-size approximation.  It avoids invoking
    # arbitrary ``__str__`` methods on untrusted values while still rejecting
    # oversized identity fields supplied by tests or adapters.
    budget = max_status_bytes
    try:
        for key in _STATUS_FIELDS:
            if key not in payload:
                continue
            value = payload[key]
            budget -= len(key.encode("utf-8")) + 4
            if budget < 0:
                return True
            if isinstance(value, str):
                budget -= len(value.encode("utf-8")) + 2
            elif isinstance(value, (bytes, bytearray, memoryview)):
                budget -= len(value) + 2
            elif isinstance(value, Mapping):
                # Nested identity/config values are not part of this schema;
                # their presence is bounded and treated as malformed later.
                budget -= 16
            else:
                budget -= 8
            if budget < 0:
                return True
    except Exception:
        return False
    return False


def _json_object_pairs(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in _STATUS_FIELDS and key in result:
            raise ValueError("duplicate status field")
        # Identity/config fields are intentionally discarded during parsing,
        # rather than copied into an intermediate status mapping.
        if key in _STATUS_FIELDS:
            result[key] = value
    return result


def _payload_to_mapping(
    payload: object,
    *,
    max_status_bytes: int,
) -> tuple[Mapping[str, object] | None, AuthCategory | None]:
    if isinstance(payload, (bytes, bytearray, memoryview)):
        raw = bytes(payload)
        if len(raw) > max_status_bytes:
            return None, AuthCategory.OVERSIZED
        try:
            payload = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None, AuthCategory.MALFORMED

    if isinstance(payload, str):
        try:
            if len(payload.encode("utf-8")) > max_status_bytes:
                return None, AuthCategory.OVERSIZED
        except UnicodeError:
            return None, AuthCategory.MALFORMED
        try:
            payload = json.loads(payload, object_pairs_hook=_json_object_pairs)
        except Exception:
            return None, AuthCategory.MALFORMED

    mapping = _normalise_status_mapping(payload)
    if mapping is None:
        # Too many direct fields are a bounded-input failure; all other shape
        # failures are simply malformed.  Neither path retains the payload.
        if isinstance(payload, Mapping):
            try:
                if len(payload) > _MAX_MAPPING_KEYS:
                    return None, AuthCategory.OVERSIZED
            except Exception:
                pass
        return None, AuthCategory.MALFORMED
    if _mapping_is_oversized(mapping, max_status_bytes=max_status_bytes):
        return None, AuthCategory.OVERSIZED
    return mapping, None


def parse_auth_status(
    payload: object,
    *,
    max_status_bytes: int = _DEFAULT_MAX_STATUS_BYTES,
) -> AuthPreflightResult:
    """Parse ``claude auth status --json`` into a sanitized result.

    ``payload`` may be a JSON string/bytes value or a mapping supplied by a
    test adapter.  Only ``loggedIn``, ``authMethod``, ``apiProvider``, and
    ``subscriptionType`` participate in the decision.  All other fields,
    including identity fields, are ignored and never copied into the result.
    """

    if not isinstance(max_status_bytes, int) or isinstance(max_status_bytes, bool):
        return _result(AuthCategory.MALFORMED)
    if max_status_bytes <= 0:
        return _result(AuthCategory.OVERSIZED)
    max_status_bytes = min(max_status_bytes, _MAX_STATUS_BYTES)

    mapping, shape_failure = _payload_to_mapping(
        payload,
        max_status_bytes=max_status_bytes,
    )
    if shape_failure is not None:
        return _result(shape_failure)
    if mapping is None:
        return _result(AuthCategory.MALFORMED)

    try:
        logged_in = mapping.get("loggedIn")
        raw_auth_method = mapping.get("authMethod")
        raw_api_provider = mapping.get("apiProvider")
        raw_subscription_type = mapping.get("subscriptionType")
        auth_method = _normalise_enum_text(raw_auth_method)
        api_provider = _normalise_enum_text(raw_api_provider)
        subscription_type = _normalise_enum_text(raw_subscription_type)
    except Exception:
        return _result(AuthCategory.MALFORMED)

    # ``False`` is a useful, explicit logged-out signal even when the CLI
    # omits the other fields.  Every non-boolean value is malformed.
    if type(logged_in) is not bool:
        return _result(AuthCategory.MALFORMED)
    if not logged_in:
        return _result(AuthCategory.LOGGED_OUT)

    if auth_method is None or api_provider is None:
        return _result(AuthCategory.MALFORMED)
    if auth_method in _API_KEY_METHODS:
        return _result(AuthCategory.API_KEY)
    if auth_method in _METERED_METHODS:
        return _result(AuthCategory.METERED)
    if api_provider in _THIRD_PARTY_PROVIDERS:
        return _result(AuthCategory.THIRD_PARTY)
    if api_provider not in _FIRST_PARTY_PROVIDERS:
        return _result(AuthCategory.UNKNOWN)
    if auth_method not in _OAUTH_METHODS:
        return _result(AuthCategory.UNKNOWN)
    if "subscriptionType" not in mapping:
        return _result(AuthCategory.MISSING_SUBSCRIPTION)
    if not isinstance(raw_subscription_type, str):
        return _result(AuthCategory.MALFORMED)
    if not raw_subscription_type.strip():
        return _result(AuthCategory.MISSING_SUBSCRIPTION)
    if subscription_type is None:
        return _result(AuthCategory.MALFORMED)
    if subscription_type in _METERED_SUBSCRIPTION_TYPES:
        return _result(AuthCategory.METERED)
    if subscription_type not in _SUBSCRIPTION_TYPES:
        return _result(AuthCategory.UNKNOWN)
    return _result(AuthCategory.SUBSCRIPTION_OAUTH)


def _bounded_timeout(value: object) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(value):
        return _DEFAULT_TIMEOUT_SECONDS
    return min(_MAX_TIMEOUT_SECONDS, max(_MIN_TIMEOUT_SECONDS, value))


def _bounded_output_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return _DEFAULT_MAX_STATUS_BYTES
    return min(_MAX_STATUS_BYTES, max(1, value))


def _probe_environment(source: Mapping[str, object] | None = None) -> dict[str, str]:
    """Build the explicit non-credential environment for the CLI child."""

    if source is None:
        source = os.environ
    environment: dict[str, str] = {}
    for key in _PROBE_ENV_KEYS:
        try:
            value = source.get(key)
        except Exception:
            continue
        if isinstance(value, str) and value and len(value) <= 4096 and "\x00" not in value:
            environment[key] = value
    # A PATH is required to resolve ``claude``.  ``os.defpath`` is a stable,
    # non-secret fallback and does not inherit arbitrary caller variables.
    environment.setdefault("PATH", os.defpath)
    return environment


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out/oversized child without retaining its output."""

    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=1.0)
    except Exception:
        pass


def _run_bounded_cli(
    argv: list[str],
    *,
    timeout: float,
    max_output_bytes: int,
    env: Mapping[str, str],
) -> _BoundedCompleted:
    """Run the default CLI path while reading at most the output budget."""

    process = subprocess.Popen(
        argv,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=dict(env),
    )
    stream = process.stdout
    if stream is None:
        _stop_process(process)
        raise OSError("missing stdout pipe")

    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise TimeoutError
            readable, _, _ = select.select([stream], [], [], remaining)
            if not readable:
                _stop_process(process)
                raise TimeoutError
            chunk = os.read(stream.fileno(), min(8192, max_output_bytes + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > max_output_bytes:
                _stop_process(process)
                return _BoundedCompleted(
                    returncode=0,
                    stdout=b"",
                    oversized=True,
                )
        try:
            returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except TimeoutError:
            _stop_process(process)
            raise
        return _BoundedCompleted(returncode=returncode, stdout=bytes(output))
    finally:
        try:
            stream.close()
        except Exception:
            pass


def probe_claude_auth(
    *,
    runner: Callable[..., Any] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = _DEFAULT_MAX_STATUS_BYTES,
    environment: Mapping[str, object] | None = None,
) -> AuthPreflightResult:
    """Run the Claude CLI auth status command and return a typed result.

    ``runner`` is an injected ``subprocess.run``-compatible callable for
    deterministic tests.  The default invocation uses an argv list, no shell,
    captured output, a bounded timeout, and an explicit environment.  Neither
    stdout nor stderr is returned, logged, or included in exceptions.
    """

    timeout = _bounded_timeout(timeout_seconds)
    output_limit = _bounded_output_limit(max_output_bytes)
    if runner is None:
        try:
            completed = _run_bounded_cli(
                list(_CLAUDE_AUTH_ARGV),
                timeout=timeout,
                max_output_bytes=output_limit,
                env=_probe_environment(environment),
            )
        except subprocess.TimeoutExpired:
            return _result(AuthCategory.TIMEOUT)
        except TimeoutError:
            return _result(AuthCategory.TIMEOUT)
        except FileNotFoundError:
            return _result(AuthCategory.CLI_MISSING)
        except Exception:
            return _result(AuthCategory.PROBE_FAILED)
        if completed.oversized:
            return _result(AuthCategory.OVERSIZED)
    else:
        command_runner = runner
        try:
            completed = command_runner(
                list(_CLAUDE_AUTH_ARGV),
                shell=False,
                check=False,
                capture_output=True,
                text=False,
                timeout=timeout,
                env=_probe_environment(environment),
            )
        except subprocess.TimeoutExpired:
            return _result(AuthCategory.TIMEOUT)
        except TimeoutError:
            return _result(AuthCategory.TIMEOUT)
        except FileNotFoundError:
            return _result(AuthCategory.CLI_MISSING)
        except Exception:
            # Do not expose exception text: subprocess errors can contain
            # command paths, environment-derived material, or provider
            # diagnostics.
            return _result(AuthCategory.PROBE_FAILED)

    try:
        return_code = completed.returncode
    except Exception:
        return _result(AuthCategory.PROBE_FAILED)
    if type(return_code) is not int:
        return _result(AuthCategory.PROBE_FAILED)
    if return_code != 0:
        return _result(AuthCategory.NONZERO)

    try:
        stdout = completed.stdout
    except Exception:
        return _result(AuthCategory.PROBE_FAILED)
    if isinstance(stdout, str):
        try:
            if len(stdout.encode("utf-8")) > output_limit:
                return _result(AuthCategory.OVERSIZED)
        except UnicodeError:
            return _result(AuthCategory.MALFORMED)
    elif isinstance(stdout, (bytes, bytearray, memoryview)):
        if len(stdout) > output_limit:
            return _result(AuthCategory.OVERSIZED)
    else:
        return _result(AuthCategory.MALFORMED)

    return parse_auth_status(stdout, max_status_bytes=output_limit)


# Descriptive aliases keep the public seam easy to discover without making
# callers depend on one spelling of "preflight".
parse_claude_auth_status = parse_auth_status
preflight_claude_auth = probe_claude_auth
check_claude_subscription_auth = probe_claude_auth


__all__ = [
    "ALLOWED_AUTH_CATEGORY",
    "AuthCategory",
    "AuthPreflightReason",
    "AuthPreflightResult",
    "check_claude_subscription_auth",
    "parse_auth_status",
    "parse_claude_auth_status",
    "preflight_claude_auth",
    "probe_claude_auth",
]

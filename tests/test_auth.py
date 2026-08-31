from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from hermes_claude_agent_sdk.auth import (
    AuthCategory,
    AuthPreflightResult,
    parse_auth_status,
    probe_claude_auth,
)


VALID_STATUS: dict[str, object] = {
    "loggedIn": True,
    "authMethod": "oauth_token",
    "apiProvider": "firstParty",
    "subscriptionType": "max",
}


def test_parser_accepts_only_first_party_oauth_subscription() -> None:
    result = parse_auth_status(VALID_STATUS)

    assert result == AuthPreflightResult(
        allowed=True,
        category=AuthCategory.SUBSCRIPTION_OAUTH,
    )
    assert result.is_allowed is True
    assert result.reason is AuthCategory.SUBSCRIPTION_OAUTH
    assert result.status is AuthCategory.SUBSCRIPTION_OAUTH


def test_parser_accepts_json_and_discards_identity_fields() -> None:
    payload = dict(VALID_STATUS)
    payload.update(
        {
            "email": "redacted-identity",
            "orgId": "redacted-org",
            "orgName": "redacted-name",
        }
    )

    result = parse_auth_status(json.dumps(payload).encode("utf-8"))

    assert result.allowed is True
    assert result.category is AuthCategory.SUBSCRIPTION_OAUTH
    rendered = repr(result)
    assert "redacted" not in rendered
    assert "email" not in rendered
    assert "orgId" not in rendered
    assert "orgName" not in rendered


@pytest.mark.parametrize(
    ("field", "value", "category"),
    [
        ("authMethod", "api_key", AuthCategory.API_KEY),
        ("authMethod", "metered", AuthCategory.METERED),
        ("apiProvider", "bedrock", AuthCategory.THIRD_PARTY),
        ("loggedIn", False, AuthCategory.LOGGED_OUT),
        ("subscriptionType", "", AuthCategory.MISSING_SUBSCRIPTION),
        ("subscriptionType", "unknown", AuthCategory.UNKNOWN),
        ("apiProvider", "unrecognized", AuthCategory.UNKNOWN),
    ],
)
def test_parser_fails_closed_for_unsupported_auth(
    field: str,
    value: object,
    category: AuthCategory,
) -> None:
    payload = dict(VALID_STATUS)
    payload[field] = value

    result = parse_auth_status(payload)

    assert result.allowed is False
    assert result.category is category


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"loggedIn": "true", **{key: VALID_STATUS[key] for key in VALID_STATUS if key != "loggedIn"}},
        {**VALID_STATUS, "authMethod": None},
        {**VALID_STATUS, "apiProvider": 1},
        {**VALID_STATUS, "subscriptionType": object()},
        '{"loggedIn": true,',
        b"not-json",
    ],
)
def test_parser_rejects_malformed_status_without_exception_leakage(
    payload: object,
) -> None:
    result = parse_auth_status(payload)

    assert result.allowed is False
    assert result.category is AuthCategory.MALFORMED


def test_parser_rejects_oversized_status() -> None:
    payload = json.dumps({**VALID_STATUS, "extra": "x" * 128})

    result = parse_auth_status(payload, max_status_bytes=64)

    assert result.allowed is False
    assert result.category is AuthCategory.OVERSIZED


class FakeRunner:
    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.argv: list[str] | None = None
        self.kwargs: dict[str, object] | None = None

    def __call__(self, argv: list[str], **kwargs: object) -> Any:
        self.argv = argv
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.result


def test_probe_uses_bounded_no_shell_argv_and_minimal_noncredential_environment() -> None:
    runner = FakeRunner(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(VALID_STATUS).encode("utf-8"),
            stderr=b"identity and diagnostics are never returned",
        )
    )

    result = probe_claude_auth(
        runner=runner,
        timeout_seconds=2,
        max_output_bytes=1024,
        environment={
            "HOME": "fixture-home",
            "PATH": "fixture-path",
            "ANTHROPIC_API_KEY": "redacted-secret",
            "CLAUDE_CODE_OAUTH_TOKEN": "redacted-secret",
        },
    )

    assert result.allowed is True
    assert result.category is AuthCategory.SUBSCRIPTION_OAUTH
    assert runner.argv == ["claude", "auth", "status", "--json"]
    assert runner.kwargs is not None
    assert runner.kwargs["shell"] is False
    assert runner.kwargs["check"] is False
    assert runner.kwargs["capture_output"] is True
    assert runner.kwargs["text"] is False
    assert runner.kwargs["timeout"] == 2.0
    child_env = runner.kwargs["env"]
    assert isinstance(child_env, dict)
    assert child_env["HOME"] == "fixture-home"
    assert child_env["PATH"] == "fixture-path"
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in child_env
    assert "identity and diagnostics" not in repr(result)


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (FileNotFoundError("/redacted/claude"), AuthCategory.CLI_MISSING),
        (TimeoutError("redacted timeout"), AuthCategory.TIMEOUT),
        (ValueError("redacted exception"), AuthCategory.PROBE_FAILED),
    ],
)
def test_probe_sanitizes_runner_failures(
    error: BaseException,
    category: AuthCategory,
) -> None:
    result = probe_claude_auth(runner=FakeRunner(error=error))

    assert result.allowed is False
    assert result.category is category
    rendered = repr(result)
    assert "redacted" not in rendered
    assert "exception" not in rendered


def test_probe_classifies_timeout_expired_without_leaking_exception() -> None:
    result = probe_claude_auth(
        runner=FakeRunner(
            error=subprocess.TimeoutExpired(
                ["claude", "auth", "status", "--json"],
                1,
                output=b"redacted-status",
                stderr=b"redacted-stderr",
            )
        )
    )

    assert result.allowed is False
    assert result.category is AuthCategory.TIMEOUT
    assert "redacted" not in repr(result)


@pytest.mark.parametrize(
    ("completed", "category"),
    [
        (SimpleNamespace(returncode=1, stdout=b"redacted-status", stderr=b"redacted"), AuthCategory.NONZERO),
        (SimpleNamespace(returncode=0, stdout=b"x" * 65, stderr=b"redacted"), AuthCategory.OVERSIZED),
        (SimpleNamespace(returncode=0, stdout=object(), stderr=b"redacted"), AuthCategory.MALFORMED),
    ],
)
def test_probe_rejects_nonzero_oversized_and_nontext_output(
    completed: object,
    category: AuthCategory,
) -> None:
    result = probe_claude_auth(runner=FakeRunner(completed), max_output_bytes=64)

    assert result.allowed is False
    assert result.category is category
    assert "redacted" not in repr(result)

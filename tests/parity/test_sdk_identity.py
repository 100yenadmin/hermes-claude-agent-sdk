from __future__ import annotations

from importlib import metadata

import pytest

from hermes_claude_agent_sdk.parity.sdk_identity import (
    SDKIdentityViolation,
    resolve_candidate_sdk_version,
)


@pytest.mark.parametrize("sdk_version", ["0.2.151"])
def test_candidate_sdk_identity_accepts_exact_supported_installed_version(
    monkeypatch: pytest.MonkeyPatch,
    sdk_version: str,
) -> None:
    observed_distributions: list[str] = []

    def installed_version(distribution: str) -> str:
        observed_distributions.append(distribution)
        return sdk_version

    monkeypatch.setattr(metadata, "version", installed_version)

    assert resolve_candidate_sdk_version(sdk_version) == sdk_version
    assert resolve_candidate_sdk_version(None) == sdk_version
    assert observed_distributions == ["claude-agent-sdk", "claude-agent-sdk"]


def test_candidate_sdk_identity_rejects_caller_and_install_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metadata, "version", lambda _: "0.2.151")

    with pytest.raises(SDKIdentityViolation, match="sdk_version_mismatch") as exc_info:
        resolve_candidate_sdk_version("0.2.144")

    assert exc_info.value.reason_code == "sdk_version_mismatch"


@pytest.mark.parametrize(
    ("candidate_version", "installed_version", "reason_code"),
    [
        ("malformed", "0.2.151", "sdk_version_malformed"),
        ("0.2.151", "malformed", "sdk_version_malformed"),
        ("0.2.143", "0.2.143", "sdk_version_unsupported"),
        ("0.2.144", "0.2.144", "sdk_version_unsupported"),
        ("0.2.152", "0.2.152", "sdk_version_unsupported"),
    ],
)
def test_candidate_sdk_identity_rejects_malformed_or_unsupported_versions(
    monkeypatch: pytest.MonkeyPatch,
    candidate_version: str,
    installed_version: str,
    reason_code: str,
) -> None:
    monkeypatch.setattr(metadata, "version", lambda _: installed_version)

    with pytest.raises(SDKIdentityViolation, match=reason_code) as exc_info:
        resolve_candidate_sdk_version(candidate_version)

    assert exc_info.value.reason_code == reason_code


def test_candidate_sdk_identity_rejects_missing_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_distribution(distribution: str) -> str:
        raise metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(metadata, "version", missing_distribution)

    with pytest.raises(
        SDKIdentityViolation,
        match="sdk_distribution_unavailable",
    ) as exc_info:
        resolve_candidate_sdk_version("0.2.151")

    assert exc_info.value.reason_code == "sdk_distribution_unavailable"

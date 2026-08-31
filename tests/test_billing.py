from __future__ import annotations

import json

import pytest

from hermes_claude_agent_sdk.billing import (
    BillingBlockReason,
    BillingMode,
    SDKBillingEvidence,
    classify_sdk_billing,
    extract_sdk_billing_evidence,
    is_metered_sdk_env_value,
    is_subscription_oauth_token,
    plan_sdk_env_overrides,
)


SYNTHETIC_API_KEY = "sk-ant-api03-SYNTHETIC-NOT-A-CREDENTIAL"
SYNTHETIC_SETUP_TOKEN = "sk-ant-oat-SYNTHETIC-NOT-A-CREDENTIAL"
SYNTHETIC_JWT = "eyJ-SYNTHETIC-NOT-A-CREDENTIAL"
SYNTHETIC_CLAUDE_CODE_TOKEN = "cc-SYNTHETIC-NOT-A-CREDENTIAL"
SYNTHETIC_UNKNOWN_TOKEN = "opaque-SYNTHETIC-NOT-A-CREDENTIAL"


def test_subscription_token_classifier_accepts_only_recognized_shapes() -> None:
    assert is_subscription_oauth_token(SYNTHETIC_SETUP_TOKEN)
    assert is_subscription_oauth_token(SYNTHETIC_JWT)
    assert is_subscription_oauth_token(SYNTHETIC_CLAUDE_CODE_TOKEN)

    assert not is_subscription_oauth_token(SYNTHETIC_API_KEY)
    assert not is_subscription_oauth_token(SYNTHETIC_UNKNOWN_TOKEN)
    assert not is_subscription_oauth_token("")
    assert not is_subscription_oauth_token(None)


@pytest.mark.parametrize(
    "key",
    (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ),
)
def test_known_metered_vectors_are_empty_child_overrides(key: str) -> None:
    overrides = plan_sdk_env_overrides({key: SYNTHETIC_API_KEY})

    assert overrides[key] == ""
    assert SYNTHETIC_API_KEY not in json.dumps(overrides, sort_keys=True)


def test_unknown_anthropic_token_shape_is_scrubbed_and_recognized_shape_is_not() -> None:
    unknown = plan_sdk_env_overrides(
        {"ANTHROPIC_TOKEN": SYNTHETIC_UNKNOWN_TOKEN}
    )
    recognized = plan_sdk_env_overrides(
        {"ANTHROPIC_TOKEN": SYNTHETIC_SETUP_TOKEN}
    )

    assert unknown["ANTHROPIC_TOKEN"] == ""
    assert "ANTHROPIC_TOKEN" not in recognized
    assert SYNTHETIC_UNKNOWN_TOKEN not in json.dumps(unknown, sort_keys=True)
    assert SYNTHETIC_SETUP_TOKEN not in json.dumps(recognized, sort_keys=True)


def test_configured_metered_vectors_cannot_rearm_billing() -> None:
    overrides = plan_sdk_env_overrides(
        {},
        {
            "ANTHROPIC_API_KEY": SYNTHETIC_API_KEY,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": 300000,
        },
    )

    assert overrides.get("ANTHROPIC_API_KEY") != SYNTHETIC_API_KEY
    assert overrides["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "300000"
    assert SYNTHETIC_API_KEY not in json.dumps(overrides, sort_keys=True)


def test_custom_anthropic_endpoint_is_scrubbed_from_parent_and_config() -> None:
    overrides = plan_sdk_env_overrides(
        {"ANTHROPIC_BASE_URL": "https://synthetic.invalid"},
        {"ANTHROPIC_BASE_URL": "https://other.invalid"},
    )

    assert overrides == {"ANTHROPIC_BASE_URL": ""}


def test_metered_classifier_is_fail_closed_for_unknown_token_shapes() -> None:
    assert is_metered_sdk_env_value("ANTHROPIC_TOKEN", SYNTHETIC_UNKNOWN_TOKEN)
    assert not is_metered_sdk_env_value("ANTHROPIC_TOKEN", SYNTHETIC_SETUP_TOKEN)
    assert not is_metered_sdk_env_value("UNRELATED_SETTING", SYNTHETIC_API_KEY)


def test_extract_system_and_rate_limit_evidence_is_bounded_and_serializable() -> None:
    system = extract_sdk_billing_evidence(
        {
            "type": "SystemMessage",
            "subtype": "init",
            "data": {"apiKeySource": "none"},
        }
    )
    rate_limit = extract_sdk_billing_evidence(
        {
            "type": "RateLimitEvent",
            "rate_limit_info": {
                "raw": {"isUsingOverage": False},
                "overage_status": "rejected",
                "rate_limit_type": "five_hour",
            },
        }
    )

    assert system is not None
    assert rate_limit is not None
    assert json.dumps(system.to_dict(), sort_keys=True)
    assert json.dumps(rate_limit.to_dict(), sort_keys=True)
    assert SYNTHETIC_API_KEY not in json.dumps(system.to_dict(), sort_keys=True)


def test_recognized_non_overage_evidence_is_included() -> None:
    result = classify_sdk_billing(
        SDKBillingEvidence(
            api_key_source="none",
            is_using_overage=False,
            overage_status="rejected",
            rate_limit_type="five_hour",
        )
    )

    assert result.allowed
    assert result.mode is BillingMode.SUBSCRIPTION_INCLUDED
    assert result.block_reason is None
    assert result.to_dict()["billing_mode"] == BillingMode.SUBSCRIPTION_INCLUDED.value


@pytest.mark.parametrize(
    "evidence, reason",
    (
        (
            SDKBillingEvidence(api_key_source="metered"),
            BillingBlockReason.API_KEY_SOURCE,
        ),
        (
            SDKBillingEvidence(is_using_overage=True),
            BillingBlockReason.EXTRA_USAGE,
        ),
        (
            SDKBillingEvidence(overage_status="allowed_warning"),
            BillingBlockReason.EXTRA_USAGE,
        ),
        (
            SDKBillingEvidence(rate_limit_type="overage"),
            BillingBlockReason.OVERAGE,
        ),
        (None, BillingBlockReason.UNKNOWN_EVIDENCE),
    ),
)
def test_metered_or_unknown_evidence_returns_typed_block(
    evidence: SDKBillingEvidence | None, reason: BillingBlockReason
) -> None:
    result = classify_sdk_billing(evidence)

    assert not result.allowed
    assert result.block_reason is reason
    assert result.to_dict()["block_reason"] == reason.value
    assert SYNTHETIC_API_KEY not in json.dumps(result.to_dict(), sort_keys=True)


def test_conflicting_evidence_blocks_before_calls() -> None:
    result = classify_sdk_billing(
        SDKBillingEvidence(api_key_source="none", is_using_overage=True)
    )

    assert not result.allowed
    assert result.block_reason is BillingBlockReason.CONFLICTING_EVIDENCE
    assert result.mode is BillingMode.UNKNOWN


def test_unknown_signal_value_does_not_downgrade_to_included() -> None:
    result = classify_sdk_billing(
        SDKBillingEvidence(api_key_source="future-provider-signal")
    )

    assert not result.allowed
    assert result.block_reason is BillingBlockReason.UNKNOWN_EVIDENCE
    assert result.mode is BillingMode.UNKNOWN


def test_billing_module_has_no_process_environment_dependency() -> None:
    from pathlib import Path

    source = Path(__file__).parents[1] / "src/hermes_claude_agent_sdk/billing.py"
    text = source.read_text(encoding="utf-8")

    assert "os.environ" not in text
    assert "os.getenv" not in text

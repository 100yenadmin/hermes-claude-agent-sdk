"""Pure, subscription-only billing policy for Claude Agent SDK children.

This module is deliberately independent of Hermes internals, the Claude SDK,
the process environment, and credential stores.  Callers provide an explicit
parent-environment mapping and typed or SDK-shaped evidence.  The returned
environment plan contains only safe operational overrides and empty-string
scrubs; evidence is reduced to bounded categories before it is returned.

The policy is extracted from ``agent/transports/claude_agent_sdk_session.py``
in ``hermes-agent-fable-orchestration-parity-v2`` (source snapshot
``226f2e17a69dd634519ba5134635e3f1d6dd7bd9``), specifically the
``_METERED_ENV_DENYLIST``, OAuth-shape classifier, SDK environment scrub, and
SDK billing evidence guard.  The standalone boundary intentionally keeps the
subscription-only fail-closed behavior and does not carry the host's private
imports or its metered opt-in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class BillingMode(str, Enum):
    """Sanitized billing labels suitable for a usage receipt."""

    SUBSCRIPTION_INCLUDED = "subscription_included"
    SDK_REPORTED_METERED = "sdk_reported_metered"
    UNKNOWN = "unknown"


class BillingBlockReason(str, Enum):
    """Why a subscription-only turn cannot proceed."""

    API_KEY_SOURCE = "api_key_source"
    EXTRA_USAGE = "extra_usage"
    OVERAGE = "overage"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


# Keep this order aligned with the source policy.  It is part of the child
# process contract: the SDK merges options on top of its inherited env, so a
# present metered vector can only be neutralized with an explicit empty value.
METERED_ENV_DENYLIST: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

# Claude Code may retry a model refusal on a different subscription model even
# when the SDK caller did not configure ``fallback_model``.  Hermes selections
# are exact and provider fallback is forbidden, so prevent that retry in the
# child instead of merely rejecting its receipt after usage has occurred.
REFUSAL_FALLBACK_ENV = "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"
REFUSAL_FALLBACK_VALUE = "1"

# Private aliases make the extraction's correspondence with the source easy
# to audit without exposing any source-specific host module.
_METERED_ENV_DENYLIST = METERED_ENV_DENYLIST
_MAX_ENV_KEY_LENGTH = 128
_MAX_ENV_VALUE_LENGTH = 4096
_MAX_EVIDENCE_TEXT_LENGTH = 64

_METERED_API_KEY_SOURCES = frozenset(
    {
        "anthropic_api_key",
        "anthropic_auth_token",
        "anthropic_token",
        "api_key",
        "api-key",
        # Canonical category accepted by the typed standalone seam.
        "metered",
    }
)
_SAFE_OVERAGE_STATUSES = frozenset(
    {"rejected", "disabled", "not_allowed", "not-allowed", "inactive", "none"}
)
_RISKY_OVERAGE_STATUSES = frozenset({"allowed", "allowed_warning", "allowed-warning"})
_SAFE_RATE_LIMIT_TYPES = frozenset(
    {
        "five_hour", "five-hour", "seven_day", "seven-day",
        # The pinned SDK distinguishes these subscription windows from overage.
        "seven_day_opus", "seven_day_sonnet",
        # Bundled CLI 2.1.258 emits this Fable subscription window even though
        # SDK 0.2.151 omits it from RateLimitType. Paid-use signals still veto it.
        "seven_day_overage_included",
    }
)

_SECRETISH_ENV_PARTS = frozenset(
    {
        "AUTH",
        "AUTHENTICATION",
        "COOKIE",
        "CREDENTIAL",
        "KEY",
        "OAUTH",
        "PASSWORD",
        "PRIVATE",
        "SECRET",
        "TOKEN",
    }
)


def is_subscription_oauth_token(value: object) -> bool:
    """Return whether *value* has a recognized subscription token shape.

    This mirrors Hermes' positive classifier without importing its credential
    adapter.  API-key-shaped ``sk-ant-api...`` values are explicitly excluded;
    all unknown and malformed values fail closed.
    """

    if not isinstance(value, str) or not value:
        return False
    if value.startswith("sk-ant-api"):
        return False
    if value.startswith("sk-ant-"):
        return True
    if value.startswith("eyJ"):
        return True
    if value.startswith("cc-"):
        return True
    return False


def is_metered_sdk_env_value(key: object, value: object) -> bool:
    """Return whether a child-environment value is a metered billing vector.

    ``ANTHROPIC_TOKEN`` is shared by Hermes' API-key and subscription setup
    token lanes.  Only the positive OAuth shapes are allowed through; every
    other non-empty value is treated as metered.  Other denylisted variables
    are always metered when present and non-empty.
    """

    if not isinstance(key, str) or key not in METERED_ENV_DENYLIST:
        return False
    if value is None or value == "":
        return False
    if key == "ANTHROPIC_TOKEN":
        return not is_subscription_oauth_token(value)
    return True


def _safe_env_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not value or len(value) > _MAX_ENV_KEY_LENGTH or "\x00" in value:
        return None
    return value


def _safe_env_value(value: object) -> str | None:
    """Stringify only bounded scalar config values.

    Environment values from the parent mapping are never returned.  This
    helper is for explicit operator knobs only and rejects arbitrary objects
    whose string representation could disclose an unbounded secret.
    """

    if value is None or not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value)
    if len(text) > _MAX_ENV_VALUE_LENGTH or "\x00" in text:
        return None
    return text


def _looks_secretish_env_key(key: str) -> bool:
    parts = {part for part in key.upper().replace("-", "_").split("_") if part}
    return bool(parts & _SECRETISH_ENV_PARTS)


def plan_sdk_env_overrides(
    parent_env: Mapping[str, object],
    configured_env: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Plan safe overrides for a spawned Claude SDK child.

    The parent mapping is inspected only for the known denylist.  Present
    metered vectors become ``key: ""`` and their values are never copied.
    Recognized subscription ``ANTHROPIC_TOKEN`` values need no override and
    therefore never appear in the returned plan.  Configured denylist or
    secret-looking values are ignored; bounded non-secret scalar knobs are
    retained after stringification.

    No process-global environment is consulted.  A malformed mapping is
    treated as empty rather than being converted into an unsanitized child
    environment.
    """

    overrides: dict[str, str] = {
        REFUSAL_FALLBACK_ENV: REFUSAL_FALLBACK_VALUE,
    }

    if isinstance(parent_env, Mapping):
        for key in METERED_ENV_DENYLIST:
            try:
                present = key in parent_env
                value = parent_env[key] if present else None
            except Exception:
                # An unreadable present value is not safe to pass through.
                present = True
                value = object()
            if present and is_metered_sdk_env_value(key, value):
                overrides[key] = ""

    if not isinstance(configured_env, Mapping):
        return overrides

    try:
        configured_items = configured_env.items()
        for raw_key, raw_value in configured_items:
            key = _safe_env_key(raw_key)
            if key is None or raw_value is None:
                continue
            # Never allow config to overwrite the scrub or place a credential
            # into the SDK options dict.  The parent mapping owns the
            # recognized subscription token flow.
            if (
                key in METERED_ENV_DENYLIST
                or key == REFUSAL_FALLBACK_ENV
                or _looks_secretish_env_key(key)
            ):
                continue
            value = _safe_env_value(raw_value)
            if value is not None:
                overrides[key] = value
    except Exception:
        # A hostile or malformed mapping cannot be allowed to defeat the
        # already-built scrub.  Keep the bounded plan accumulated so far.
        return overrides

    return overrides


@dataclass(frozen=True, slots=True)
class SDKBillingEvidence:
    """Typed, sanitized SDK billing signals.

    Values may be supplied by a host adapter or produced by
    :func:`extract_sdk_billing_evidence`.  ``to_dict`` emits only canonical
    categories and field names; it never emits arbitrary SDK payloads.
    """

    api_key_source: object | None = None
    is_using_overage: object | None = None
    overage_status: object | None = None
    rate_limit_type: object | None = None
    malformed_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        normalized, malformed = _normalize_evidence(self)
        out: dict[str, object] = {}
        if normalized.api_key_source is not None:
            out["api_key_source"] = normalized.api_key_source
        if normalized.is_using_overage is not None:
            out["is_using_overage"] = normalized.is_using_overage
        if normalized.overage_status is not None:
            out["overage_status"] = normalized.overage_status
        if normalized.rate_limit_type is not None:
            out["rate_limit_type"] = normalized.rate_limit_type
        if malformed:
            out["malformed_fields"] = list(malformed)
        return out


def _read_field(value: object, *names: str, default: object = None) -> object:
    for name in names:
        if isinstance(value, Mapping):
            try:
                if name in value:
                    return value[name]
            except Exception:
                return default
        else:
            try:
                candidate = getattr(value, name)
            except Exception:
                continue
            return candidate
    return default


def _bounded_signal_text(value: object) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if not isinstance(value, str):
        return "unknown", True
    text = value.strip().lower()
    if not text or len(text) > _MAX_EVIDENCE_TEXT_LENGTH:
        return "unknown", True
    return text, False


def _canonical_api_key_source(value: object) -> tuple[str | None, bool]:
    text, malformed = _bounded_signal_text(value)
    if text is None:
        return None, malformed
    if text == "none":
        return "none", malformed
    if text in _METERED_API_KEY_SOURCES:
        return "metered", malformed
    return "unknown", malformed


def _canonical_overage_status(value: object) -> tuple[str | None, bool]:
    text, malformed = _bounded_signal_text(value)
    if text is None:
        return None, malformed
    if text in _SAFE_OVERAGE_STATUSES or text in _RISKY_OVERAGE_STATUSES:
        return text, malformed
    return "unknown", malformed


def _canonical_rate_limit_type(value: object) -> tuple[str | None, bool]:
    text, malformed = _bounded_signal_text(value)
    if text is None:
        return None, malformed
    if text in _SAFE_RATE_LIMIT_TYPES:
        return text, malformed
    if text == "overage":
        return text, malformed
    return "unknown", malformed


def _normalize_evidence(
    evidence: SDKBillingEvidence,
) -> tuple[SDKBillingEvidence, tuple[str, ...]]:
    raw_malformed = evidence.malformed_fields
    if isinstance(raw_malformed, (tuple, list, set, frozenset)):
        malformed: set[str] = {
            name
            for name in raw_malformed
            if isinstance(name, str)
            and name
            in {
                "api_key_source",
                "is_using_overage",
                "overage_status",
                "rate_limit_type",
            }
        }
    else:
        # The typed seam itself was malformed; expose only a safe field name.
        malformed = {"malformed_fields"}

    api_key_source, bad = _canonical_api_key_source(evidence.api_key_source)
    if bad:
        malformed.add("api_key_source")

    is_using_overage = evidence.is_using_overage
    if is_using_overage is not None and not isinstance(is_using_overage, bool):
        is_using_overage = None
        malformed.add("is_using_overage")

    overage_status, bad = _canonical_overage_status(evidence.overage_status)
    if bad:
        malformed.add("overage_status")

    rate_limit_type, bad = _canonical_rate_limit_type(evidence.rate_limit_type)
    if bad:
        malformed.add("rate_limit_type")

    normalized = SDKBillingEvidence(
        api_key_source=api_key_source,
        is_using_overage=is_using_overage,
        overage_status=overage_status,
        rate_limit_type=rate_limit_type,
    )
    return normalized, tuple(sorted(malformed))


def _event_kind(event: object) -> str:
    if isinstance(event, Mapping):
        value = _read_field(event, "type", "kind", default="")
    else:
        value = type(event).__name__
    if not isinstance(value, str):
        return ""
    return value.replace("-", "_").replace(" ", "").lower()


def extract_sdk_billing_evidence(event: object) -> SDKBillingEvidence | None:
    """Extract only the four supported billing fields from one SDK event.

    Both SDK-shaped objects and synthetic mappings are accepted so host code
    can adapt the pinned SDK without importing it here.  Any unsupported event
    returns ``None``; malformed fields are represented by safe field names and
    subsequently block classification.  Arbitrary event data, messages,
    tokens, and exception text are never retained.
    """

    if isinstance(event, SDKBillingEvidence):
        return event

    kind = _event_kind(event)
    subtype = _read_field(event, "subtype", default=None)
    direct_has_api_source = isinstance(event, Mapping) and any(
        key in event for key in ("apiKeySource", "api_key_source")
    )
    is_system = kind in {"systemmessage", "system_message", "system"} or (
        direct_has_api_source and (subtype in (None, "init"))
    )
    if is_system:
        if subtype not in (None, "init"):
            return None
        data = _read_field(event, "data", default=event)
        if not isinstance(data, Mapping):
            return SDKBillingEvidence(malformed_fields=("api_key_source",))
        source = _read_field(data, "apiKeySource", "api_key_source", default=None)
        return SDKBillingEvidence(api_key_source=source)

    info = _read_field(event, "rate_limit_info", "rateLimitInfo", default=None)
    is_rate_limit = kind in {"ratelimitevent", "rate_limit_event", "rate_limit"}
    if info is None and isinstance(event, Mapping):
        is_rate_limit = is_rate_limit or any(
            key in event
            for key in (
                "raw",
                "isUsingOverage",
                "is_using_overage",
                "overageStatus",
                "overage_status",
                "rateLimitType",
                "rate_limit_type",
            )
        )
        info = event
    if not is_rate_limit:
        return None

    raw = _read_field(info, "raw", default={})
    malformed: list[str] = []
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        malformed.append("is_using_overage")
        raw = {}

    overage_value = _read_field(
        info,
        "is_using_overage",
        "isUsingOverage",
        default=_read_field(raw, "isUsingOverage", "is_using_overage", default=None),
    )
    overage_status = _read_field(
        info,
        "overage_status",
        "overageStatus",
        default=_read_field(raw, "overageStatus", "overage_status", default=None),
    )
    rate_limit_type = _read_field(
        info,
        "rate_limit_type",
        "rateLimitType",
        default=_read_field(raw, "rateLimitType", "rate_limit_type", default=None),
    )
    if overage_value is not None and not isinstance(overage_value, bool):
        malformed.append("is_using_overage")
    return SDKBillingEvidence(
        is_using_overage=overage_value,
        overage_status=overage_status,
        rate_limit_type=rate_limit_type,
        malformed_fields=tuple(sorted(set(malformed))),
    )


def _coerce_evidence(value: object) -> SDKBillingEvidence | None:
    if isinstance(value, SDKBillingEvidence):
        return value
    if not isinstance(value, Mapping):
        return None
    return SDKBillingEvidence(
        api_key_source=_read_field(value, "api_key_source", "apiKeySource", default=None),
        is_using_overage=_read_field(
            value, "is_using_overage", "isUsingOverage", default=None
        ),
        overage_status=_read_field(value, "overage_status", "overageStatus", default=None),
        rate_limit_type=_read_field(
            value, "rate_limit_type", "rateLimitType", default=None
        ),
    )


def _blocked(
    reason: BillingBlockReason,
    evidence: SDKBillingEvidence,
    *,
    metered: bool = False,
) -> "BillingDecision":
    return BillingDecision(
        mode=(BillingMode.SDK_REPORTED_METERED if metered else BillingMode.UNKNOWN),
        allowed=False,
        block_reason=reason,
        evidence=evidence,
    )


@dataclass(frozen=True, slots=True)
class BillingDecision:
    """Typed result the host must gate on before making another SDK call."""

    mode: BillingMode
    allowed: bool
    block_reason: BillingBlockReason | None
    evidence: SDKBillingEvidence

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "billing_mode": self.mode.value,
            "block_reason": (
                self.block_reason.value if self.block_reason is not None else None
            ),
            "evidence": self.evidence.to_dict(),
        }


def classify_sdk_billing(evidence: object) -> BillingDecision:
    """Classify one SDK evidence snapshot with a subscription-only gate.

    The only allowed result is a recognized non-overage subscription signal.
    API-key source, active or enabled Extra Usage, overage, unknown/malformed,
    and contradictory signals all return ``allowed=False``.  Callers should
    return the typed result to the host and stop before making further SDK or
    model calls when it is blocked.
    """

    typed = _coerce_evidence(evidence)
    if typed is None:
        return _blocked(BillingBlockReason.UNKNOWN_EVIDENCE, SDKBillingEvidence())

    normalized, malformed = _normalize_evidence(typed)
    if malformed:
        return _blocked(BillingBlockReason.UNKNOWN_EVIDENCE, normalized)

    source = normalized.api_key_source
    using_overage = normalized.is_using_overage
    status = normalized.overage_status
    rate_limit_type = normalized.rate_limit_type

    if source == "none" and (using_overage is True or status in _RISKY_OVERAGE_STATUSES):
        return _blocked(BillingBlockReason.CONFLICTING_EVIDENCE, normalized)
    if using_overage is False and status in _RISKY_OVERAGE_STATUSES:
        return _blocked(BillingBlockReason.CONFLICTING_EVIDENCE, normalized)
    if source == "metered":
        return _blocked(BillingBlockReason.API_KEY_SOURCE, normalized, metered=True)
    if using_overage is True or status in _RISKY_OVERAGE_STATUSES:
        return _blocked(BillingBlockReason.EXTRA_USAGE, normalized, metered=True)
    if rate_limit_type == "overage" and using_overage is not False:
        return _blocked(BillingBlockReason.OVERAGE, normalized, metered=True)

    values = (source, using_overage, status, rate_limit_type)
    if any(value == "unknown" for value in values):
        return _blocked(BillingBlockReason.UNKNOWN_EVIDENCE, normalized)
    if not any(
        (
            source == "none",
            using_overage is False,
            status in _SAFE_OVERAGE_STATUSES,
            rate_limit_type in _SAFE_RATE_LIMIT_TYPES,
        )
    ):
        return _blocked(BillingBlockReason.UNKNOWN_EVIDENCE, normalized)

    return BillingDecision(
        mode=BillingMode.SUBSCRIPTION_INCLUDED,
        allowed=True,
        block_reason=None,
        evidence=normalized,
    )


# Friendly names for host adapters that prefer policy-oriented verbs.
classify_subscription_billing = classify_sdk_billing
build_sdk_env_overrides = plan_sdk_env_overrides

# Source-shaped private names are retained as tiny pure aliases for adapters
# that are being ported incrementally.  Unlike the source functions, these
# aliases still require explicit mappings and never consult host state.
_is_subscription_oauth_token = is_subscription_oauth_token
_is_metered_sdk_env_value = is_metered_sdk_env_value


def _scrubbed_sdk_env(parent_env: Mapping[str, object]) -> dict[str, str]:
    """Return only the empty-string scrubs for present parent vectors."""

    planned = plan_sdk_env_overrides(parent_env)
    return {key: value for key, value in planned.items() if key in METERED_ENV_DENYLIST}


def _sdk_env_overrides(
    parent_env: Mapping[str, object],
    configured_env: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Source-shaped pure alias for :func:`plan_sdk_env_overrides`."""

    return plan_sdk_env_overrides(parent_env, configured_env)


SdkBillingEvidence = SDKBillingEvidence


__all__ = [
    "BillingBlockReason",
    "BillingDecision",
    "BillingMode",
    "METERED_ENV_DENYLIST",
    "SDKBillingEvidence",
    "SdkBillingEvidence",
    "build_sdk_env_overrides",
    "classify_sdk_billing",
    "classify_subscription_billing",
    "extract_sdk_billing_evidence",
    "is_metered_sdk_env_value",
    "is_subscription_oauth_token",
    "plan_sdk_env_overrides",
]

"""Fail-closed model provenance checks shared by live parity suites."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


_AUTHORIZED_CANONICAL_MODELS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "claude-fable-5": frozenset({"claude-fable-5-1"}),
    }
)


def is_silent_model_fallback(result: Mapping[str, Any], *, model: str) -> bool:
    """Reject missing, ambiguous, or non-selected model provenance.

    ``RuntimeUsageReceipt.model`` is the SDK-observed billing identity and may
    be canonicalized from the selected request. Parity therefore validates the
    explicit selected/effective/resolution fields instead of requiring the
    legacy billing identity to equal the selected model.
    """
    if (
        result.get("provider") != "claude-agent-sdk"
        or result.get("selected_model") != model
        or result.get("effective_model") != model
    ):
        return True
    resolution = result.get("model_resolution")
    canonical = result.get("canonical_model")
    observed = result.get("model")
    if resolution == "exact":
        return observed != model or canonical not in (None, "unknown", model)
    if resolution == "canonicalized":
        authorized = _AUTHORIZED_CANONICAL_MODELS.get(model, frozenset())
        return (
            not isinstance(canonical, str)
            or canonical in ("", "unknown", model)
            or canonical not in authorized
            or observed != canonical
        )
    return True


def is_silent_receipt_model_fallback(receipt: Any, *, model: str) -> bool:
    """Apply the model provenance check to a host usage receipt."""
    return is_silent_model_fallback(
        {
            "provider": getattr(receipt, "provider", None),
            "model": getattr(receipt, "model", None),
            "selected_model": getattr(receipt, "selected_model", None),
            "effective_model": getattr(receipt, "effective_model", None),
            "canonical_model": getattr(receipt, "canonical_model", None),
            "model_resolution": getattr(receipt, "model_resolution", None),
        },
        model=model,
    )

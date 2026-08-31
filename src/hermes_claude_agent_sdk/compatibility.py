"""AgentRuntime v1 compatibility metadata for the Claude runtime plugin.

This module deliberately imports the Hermes host contract only when a host
asks for a descriptor or a doctor report.  Importing the plugin entry point is
therefore safe in a clean Python environment and never imports the Claude SDK.
"""

from __future__ import annotations

import json
from importlib import metadata
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - imports are documentation-only at runtime
    from agent.runtime_api import RuntimeDescriptor


PLUGIN_VERSION = "0.1.0rc1"
RUNTIME_ID = "hermes-claude-agent-sdk"
SDK_DISTRIBUTION = "claude-agent-sdk"
SDK_VERSION = "0.2.144"

# The extracted runtime will need the complete v1 host facade.  Declaring the
# concrete set here lets the host reject an incomplete installation before a
# factory or SDK client can be activated.
REQUIRED_HOST_CAPABILITIES = frozenset(
    {
        "background_delivery_v1",
        "cancellation_v1",
        "compaction_events_v1",
        "host_approval_v1",
        "host_status_v1",
        "host_tool_execution_v1",
        "runtime_state_v1",
        "usage_receipts_v1",
    }
)

# The provider id is owned by this independently packaged runtime.  It is
# intentionally distinct from the host's ``anthropic`` Messages provider: the
# SDK owns a whole-turn agent loop, so routing must use the provider-neutral
# runtime mode rather than a transport-specific Messages mode.
PROVIDER_IDS = frozenset({"claude-agent-sdk"})
API_MODES = frozenset({"agent_runtime"})
MODEL_PREFIXES = ("claude-", "anthropic/claude-")


def build_runtime_descriptor() -> "RuntimeDescriptor":
    """Build the concrete v1 descriptor without touching SDK or credentials."""

    from agent.runtime_api import CompactionOwnership, RuntimeDescriptor

    return RuntimeDescriptor(
        runtime_id=RUNTIME_ID,
        plugin_version=PLUGIN_VERSION,
        runtime_api_min=1,
        runtime_api_max=1,
        required_host_capabilities=REQUIRED_HOST_CAPABILITIES,
        provider_ids=PROVIDER_IDS,
        api_modes=API_MODES,
        session_state_schema_version=1,
        model_prefixes=MODEL_PREFIXES,
        compaction_ownership=CompactionOwnership.RUNTIME_NATIVE,
    )


# A short alias is convenient for plugin authors and keeps the public surface
# discoverable without exposing a mutable module-level descriptor instance.
runtime_descriptor = build_runtime_descriptor


def _sdk_metadata() -> dict[str, Any]:
    """Read package metadata only; never import or instantiate the SDK."""

    try:
        installed = metadata.version(SDK_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        installed = None
    except Exception:
        installed = None
    return {
        "distribution": SDK_DISTRIBUTION,
        "required_version": SDK_VERSION,
        "installed_version": installed,
        "compatible": installed == SDK_VERSION,
    }


def doctor(host_manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a credential-free API/capability compatibility report.

    ``host_manifest`` is optional to support offline diagnostics and tests.
    When omitted, the report uses the public host
    :func:`agent.runtime_api.runtime_api_manifest` function.  The SDK entry is
    metadata-only and is intentionally separate from host compatibility so a
    caller can diagnose the seam even before dependencies are installed.
    """

    try:
        descriptor = build_runtime_descriptor()
        from agent.runtime_api import runtime_api_manifest
    except (ImportError, ModuleNotFoundError) as exc:
        return {
            "status": "host_unavailable",
            "compatible": False,
            "runtime_id": RUNTIME_ID,
            "plugin_version": PLUGIN_VERSION,
            "error": "public Hermes AgentRuntime v1 API is unavailable",
            "error_type": type(exc).__name__,
            "sdk": _sdk_metadata(),
        }

    if host_manifest is None:
        try:
            host_manifest = runtime_api_manifest()
        except Exception as exc:
            return {
                "status": "host_unavailable",
                "compatible": False,
                "runtime_id": descriptor.runtime_id,
                "plugin_version": descriptor.plugin_version,
                "error": "public Hermes AgentRuntime v1 manifest could not be read",
                "error_type": type(exc).__name__,
                "sdk": _sdk_metadata(),
            }

    if not isinstance(host_manifest, Mapping):
        return {
            "status": "incompatible",
            "compatible": False,
            "runtime_id": descriptor.runtime_id,
            "plugin_version": descriptor.plugin_version,
            "error": "host manifest must be a mapping",
            "sdk": _sdk_metadata(),
        }

    raw_host_version = host_manifest.get("runtime_api_version")
    try:
        host_version = int(raw_host_version)
    except (TypeError, ValueError):
        host_version = None
    raw_capabilities = host_manifest.get("host_capabilities", ())
    host_capabilities = frozenset(
        item for item in raw_capabilities if isinstance(item, str)
    ) if isinstance(raw_capabilities, (list, tuple, set, frozenset)) else frozenset()

    api_compatible = host_version is not None and (
        descriptor.runtime_api_min <= host_version <= descriptor.runtime_api_max
    )
    missing = sorted(descriptor.required_host_capabilities - host_capabilities)
    capabilities_compatible = not missing
    compatible = api_compatible and capabilities_compatible

    return {
        "status": "compatible" if compatible else "incompatible",
        "compatible": compatible,
        "runtime_id": descriptor.runtime_id,
        "plugin_version": descriptor.plugin_version,
        "runtime_api": {
            "plugin_min": descriptor.runtime_api_min,
            "plugin_max": descriptor.runtime_api_max,
            "host": host_version,
            "compatible": api_compatible,
        },
        "capabilities": {
            "required": sorted(descriptor.required_host_capabilities),
            "host": sorted(host_capabilities),
            "missing": missing,
            "compatible": capabilities_compatible,
        },
        "selectors": {
            "providers": sorted(descriptor.provider_ids),
            "api_modes": sorted(descriptor.api_modes),
            "model_prefixes": list(descriptor.model_prefixes),
        },
        "sdk": _sdk_metadata(),
    }


def doctor_json(host_manifest: Mapping[str, Any] | None = None) -> str:
    """Serialize :func:`doctor` as stable JSON for shell integrations."""

    return json.dumps(doctor(host_manifest), sort_keys=True, separators=(",", ":"))


# Explicit name for callers that want to distinguish the report from a CLI
# command.  Both names intentionally retain the same side-effect-free path.
check_compatibility = doctor


__all__ = [
    "API_MODES",
    "MODEL_PREFIXES",
    "PLUGIN_VERSION",
    "PROVIDER_IDS",
    "REQUIRED_HOST_CAPABILITIES",
    "RUNTIME_ID",
    "SDK_DISTRIBUTION",
    "SDK_VERSION",
    "build_runtime_descriptor",
    "check_compatibility",
    "doctor",
    "doctor_json",
    "runtime_descriptor",
]

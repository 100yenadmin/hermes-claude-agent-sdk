"""AgentRuntime v1 compatibility metadata for the Claude runtime plugin.

This module deliberately imports the Hermes host contract only when a host
asks for a descriptor or a doctor report.  Importing the plugin entry point is
therefore safe in a clean Python environment and never imports the Claude SDK.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import re
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - imports are documentation-only at runtime
    from agent.runtime_api import RuntimeDescriptor


PLUGIN_VERSION = "0.1.0rc1"
RUNTIME_ID = "hermes-claude-agent-sdk"
SDK_DISTRIBUTION = "claude-agent-sdk"
# ``SDK_VERSION`` remains the immutable exact dependency used by the frozen
# parity-v2 lane.  The standalone package policy is now a bounded range so a
# newer bundled Claude Code can be admitted for the successor model.
SDK_VERSION = "0.2.144"
SDK_MIN_VERSION = "0.2.144"
SDK_MAX_VERSION = "0.2.152"
FABLE_51_MODEL_ID = "claude-fable-5-1"
FABLE_51_MIN_SDK_VERSION = "0.2.151"
FABLE_51_MIN_CLI_VERSION = "2.1.257"
_CLI_VERSION_RESOURCE = "claude_agent_sdk/_cli_version.py"
_CLI_BUNDLE_RESOURCE = "claude_agent_sdk/_bundled/claude"
_CLI_BUNDLE_RESOURCE_WINDOWS = "claude_agent_sdk/_bundled/claude.exe"
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

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
        "provider_profile_registration_v1",
        "runtime_model_provenance_v1",
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
MODEL_PREFIXES = ("claude-",)
_DIRECT_MODEL_ID = re.compile(r"^claude-[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def is_supported_model_id(model: Any) -> bool:
    """Accept only bounded direct Claude ids, never provider-route slugs."""
    return (
        isinstance(model, str)
        and len(model) <= 128
        and _DIRECT_MODEL_ID.fullmatch(model) is not None
    )


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


def _bundled_cli_resource() -> str:
    """Return the exact bundled CLI resource name used by the SDK transport."""

    if platform.system() == "Windows":
        return _CLI_BUNDLE_RESOURCE_WINDOWS
    return _CLI_BUNDLE_RESOURCE


def _resolve_bundled_cli_from_distribution(
    distribution: Any,
    *,
    resource: str,
) -> str | None:
    """Resolve one safe absolute executable from distribution file metadata."""

    try:
        files = distribution.files
        candidates = [
            item
            for item in (files or ())
            if str(item).replace("\\", "/") == resource
        ]
    except Exception:
        return None
    if len(candidates) != 1:
        return None

    try:
        located = distribution.locate_file(candidates[0])
        raw_path = os.fspath(located)
    except Exception:
        return None
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        return None

    try:
        path = Path(raw_path)
        if not path.is_absolute():
            return None
        if path.name != resource.rsplit("/", 1)[-1]:
            return None
        if not path.is_file() or not os.access(path, os.X_OK):
            return None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return str(path)


def resolve_bundled_cli(*, distribution: Any | None = None) -> str | None:
    """Return the SDK transport's bundled Claude CLI absolute path.

    The resolver reads only ``importlib.metadata`` file records and never
    imports ``claude_agent_sdk``. Missing, duplicate, malformed, non-file,
    and non-executable resources all fail closed with ``None``.
    """

    if distribution is None:
        try:
            distribution = metadata.distribution(SDK_DISTRIBUTION)
        except Exception:
            return None
    return _resolve_bundled_cli_from_distribution(
        distribution,
        resource=_bundled_cli_resource(),
    )


def _version_tuple(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _range_version_tuple(value: object) -> tuple[int, int, int] | None:
    """Parse a three-part version or a two-part exclusive upper bound."""

    parsed = _version_tuple(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str) and re.fullmatch(
        r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", value
    ):
        major, minor = (int(part) for part in value.split("."))
        return major, minor, 0
    return None


def _read_bundled_cli_source(distribution: Any) -> str | None:
    """Read the SDK's version declaration without importing its package."""

    # A small fake distribution seam keeps this check deterministic in tests.
    # Real importlib.metadata distributions expose package files through
    # ``locate_file``; ``read_text`` only covers dist-info files and therefore
    # is not sufficient by itself for the package resource.
    try:
        source = distribution.read_text(_CLI_VERSION_RESOURCE)
    except Exception:
        source = None
    if isinstance(source, str):
        return source

    try:
        files = distribution.files
        candidates = [
            item
            for item in (files or ())
            if str(item).replace("\\", "/") == _CLI_VERSION_RESOURCE
        ]
        if len(candidates) != 1:
            return None
        path = distribution.locate_file(candidates[0])
        source = path.read_text(encoding="utf-8")
    except Exception:
        return None
    return source if isinstance(source, str) else None


def _parse_bundled_cli_version(source: object) -> str | None:
    """Extract one strict ``__cli_version__`` literal from package source."""

    if not isinstance(source, str):
        return None
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return None

    # The published resource has exactly one of two inert shapes: a literal
    # assignment by itself, or a leading module docstring followed by that
    # assignment.  Any additional or reordered statement fails closed.
    body = list(tree.body)
    if len(body) == 2:
        docstring = body.pop(0)
        if not (
            isinstance(docstring, ast.Expr)
            and isinstance(docstring.value, ast.Constant)
            and isinstance(docstring.value.value, str)
        ):
            return None
    if len(body) != 1:
        return None

    assignment = body[0]
    if not (
        isinstance(assignment, ast.Assign)
        and len(assignment.targets) == 1
        and isinstance(assignment.targets[0], ast.Name)
        and assignment.targets[0].id == "__cli_version__"
        and isinstance(assignment.value, ast.Constant)
    ):
        return None
    candidate = assignment.value.value
    return candidate if _version_tuple(candidate) is not None else None


def _sdk_metadata() -> dict[str, Any]:
    """Read bounded SDK/bundled-CLI metadata without importing the SDK."""

    base: dict[str, Any] = {
        "distribution": SDK_DISTRIBUTION,
        # This field documents the immutable frozen-v2 cell.  It is not the
        # successor's minimum; callers should use the explicit range below.
        "required_version": SDK_VERSION,
        "minimum_version": SDK_MIN_VERSION,
        "maximum_version_exclusive": SDK_MAX_VERSION,
        "installed_version": None,
        "bundled_cli_version": None,
        "compatible": False,
        "metadata_status": "missing",
    }
    try:
        distribution = metadata.distribution(SDK_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return base
    except Exception:
        base["metadata_status"] = "unavailable"
        return base

    try:
        sdk_version = getattr(distribution, "version", None)
    except Exception:
        base["metadata_status"] = "unavailable"
        return base
    sdk_parsed = _version_tuple(sdk_version)
    if sdk_parsed is None:
        base["metadata_status"] = "malformed"
        return base
    base["installed_version"] = sdk_version

    cli_version = _parse_bundled_cli_version(
        _read_bundled_cli_source(distribution)
    )
    base["bundled_cli_version"] = cli_version
    if cli_version is None:
        base["metadata_status"] = "malformed"
        return base

    cli_parsed = _version_tuple(cli_version)
    assert cli_parsed is not None
    minimum_sdk = _range_version_tuple(SDK_MIN_VERSION)
    maximum_sdk = _range_version_tuple(SDK_MAX_VERSION)
    assert minimum_sdk is not None and maximum_sdk is not None
    if not (minimum_sdk <= sdk_parsed < maximum_sdk):
        base["metadata_status"] = "unsupported"
        return base

    base["compatible"] = True
    base["metadata_status"] = "compatible"
    return base


def check_model_compatibility(
    model: object,
    *,
    sdk_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, model-specific SDK/CLI compatibility decision.

    Fable 5 keeps the historical behavior and does not require a successor
    catalog. Direct Fable 5.1 is admitted only when the installed
    distribution version and its bundled CLI declaration meet the recorded
    floors. The check is metadata-only and never imports the SDK.
    """

    result: dict[str, Any] = {
        "model": model if isinstance(model, str) else "unknown",
        "compatible": True,
        "status": "not_required",
        "reason": None,
        "required_sdk": FABLE_51_MIN_SDK_VERSION,
        "required_bundled_cli": FABLE_51_MIN_CLI_VERSION,
    }
    if model != FABLE_51_MODEL_ID:
        return result

    result["status"] = "incompatible"
    result["compatible"] = False
    report = sdk_metadata if sdk_metadata is not None else _sdk_metadata()
    if not isinstance(report, Mapping):
        result["compatible"] = False
        result["reason"] = "metadata_unavailable"
        return result
    metadata_status = report.get("metadata_status")
    if metadata_status is not None and metadata_status != "compatible":
        result["reason"] = "metadata_unavailable"
        return result

    sdk_version = report.get("installed_version")
    cli_version = report.get("bundled_cli_version")
    sdk_parsed = _version_tuple(sdk_version)
    cli_parsed = _version_tuple(cli_version)
    minimum_sdk = _version_tuple(FABLE_51_MIN_SDK_VERSION)
    maximum_sdk = _range_version_tuple(SDK_MAX_VERSION)
    minimum_cli = _version_tuple(FABLE_51_MIN_CLI_VERSION)
    if sdk_parsed is None:
        result["reason"] = "sdk_metadata_missing_or_malformed"
        return result
    assert minimum_sdk is not None and maximum_sdk is not None
    if not (minimum_sdk <= sdk_parsed < maximum_sdk):
        result["reason"] = "sdk_version_unsupported"
        return result
    if cli_parsed is None:
        result["reason"] = "bundled_cli_metadata_missing_or_malformed"
        return result
    if cli_parsed < minimum_cli:
        result["reason"] = "bundled_cli_version_unsupported"
        return result

    result["compatible"] = True
    result["status"] = "compatible"
    result["reason"] = None
    return result


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
            "sdk": _doctor_sdk_metadata(),
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
                "sdk": _doctor_sdk_metadata(),
            }

    if not isinstance(host_manifest, Mapping):
        return {
            "status": "incompatible",
            "compatible": False,
            "runtime_id": descriptor.runtime_id,
            "plugin_version": descriptor.plugin_version,
            "error": "host manifest must be a mapping",
            "sdk": _doctor_sdk_metadata(),
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
        "sdk": _doctor_sdk_metadata(),
    }


def _doctor_sdk_metadata() -> dict[str, Any]:
    """Add the successor decision to the credential-free SDK report."""

    report = _sdk_metadata()
    # Copy the mapping so a future metadata implementation cannot be mutated
    # by report decoration. All values come from strict versions or constants.
    result = dict(report)
    result["fable_5_1"] = check_model_compatibility(
        FABLE_51_MODEL_ID, sdk_metadata=report
    )
    return result


def doctor_json(host_manifest: Mapping[str, Any] | None = None) -> str:
    """Serialize :func:`doctor` as stable JSON for shell integrations."""

    return json.dumps(doctor(host_manifest), sort_keys=True, separators=(",", ":"))


# Explicit name for callers that want to distinguish the report from a CLI
# command.  Both names intentionally retain the same side-effect-free path.
check_compatibility = doctor


__all__ = [
    "API_MODES",
    "FABLE_51_MIN_CLI_VERSION",
    "FABLE_51_MIN_SDK_VERSION",
    "FABLE_51_MODEL_ID",
    "MODEL_PREFIXES",
    "PLUGIN_VERSION",
    "PROVIDER_IDS",
    "REQUIRED_HOST_CAPABILITIES",
    "RUNTIME_ID",
    "SDK_DISTRIBUTION",
    "SDK_MAX_VERSION",
    "SDK_MIN_VERSION",
    "SDK_VERSION",
    "build_runtime_descriptor",
    "check_compatibility",
    "check_model_compatibility",
    "doctor",
    "doctor_json",
    "resolve_bundled_cli",
    "runtime_descriptor",
]

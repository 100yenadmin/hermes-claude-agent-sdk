"""Strict, metadata-only declared and observed tool inventories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .canonical import CanonicalizationError, canonical_sha256, validate_identifier, validate_sha256

_DECLARED = frozenset({"schema_version", "tools", "mcp_servers", "declared_inventory_sha256"})
_TOOL = frozenset({"name", "schema_sha256", "declared_by", "enabled"})
_ENTRY = frozenset({"name", "schema_sha256", "enabled"})
_OBSERVED = frozenset({
    "candidate_sha256", "tools", "mcp_servers", "observed_inventory_sha256",
    "unknown_names", "missing_names", "schema_drift_names",
})
_SIDES = frozenset({"host", "plugin"})


class InventoryValidationError(CanonicalizationError):
    """Raised when a declared or observed snapshot is malformed."""


def _object(value: Any, field: str, keys: frozenset[str]) -> Mapping[str, Any]:
    try:
        actual = set(value) if isinstance(value, Mapping) else None
    except (TypeError, ValueError):
        actual = None
    if actual != keys:
        raise InventoryValidationError(f"{field} has unexpected fields")
    return value


def _array(value: Any, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise InventoryValidationError(f"{field} must be an array")
    return tuple(value)


def _name(value: Any, field: str) -> str:
    try:
        return validate_identifier(value, field=field, max_length=128)
    except CanonicalizationError as exc:
        raise InventoryValidationError(str(exc)) from exc


def _sha(value: Any, field: str) -> str:
    try:
        return validate_sha256(value, field=field)
    except CanonicalizationError as exc:
        raise InventoryValidationError(str(exc)) from exc


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise InventoryValidationError("enabled must be a boolean")
    return value


def _sorted_names(value: Any, field: str) -> tuple[str, ...]:
    names = tuple(_name(item, f"{field} item") for item in _array(value, field))
    if len(set(names)) != len(names):
        raise InventoryValidationError(f"{field} contains duplicate names")
    if names != tuple(sorted(names)):
        raise InventoryValidationError(f"{field} must be sorted")
    return names


def _entry_dict(value: Any, field: str, keys: frozenset[str]) -> Mapping[str, Any]:
    return _object(value, field, keys)


@dataclass(frozen=True, slots=True)
class ToolInventoryEntry:
    name: str
    schema_sha256: str
    declared_by: str
    enabled: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "tool name"))
        object.__setattr__(self, "schema_sha256", _sha(self.schema_sha256, "schema_sha256"))
        if not isinstance(self.declared_by, str) or self.declared_by not in _SIDES:
            raise InventoryValidationError("declared_by must be host or plugin")
        object.__setattr__(self, "enabled", _bool(self.enabled))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "schema_sha256": self.schema_sha256,
                "declared_by": self.declared_by, "enabled": self.enabled}


@dataclass(frozen=True, slots=True)
class MCPServerInventoryEntry:
    name: str
    schema_sha256: str
    enabled: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "MCP server name"))
        object.__setattr__(self, "schema_sha256", _sha(self.schema_sha256, "schema_sha256"))
        object.__setattr__(self, "enabled", _bool(self.enabled))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "schema_sha256": self.schema_sha256, "enabled": self.enabled}


@dataclass(frozen=True, slots=True)
class ObservedInventoryEntry:
    name: str
    schema_sha256: str
    enabled: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "inventory name"))
        object.__setattr__(self, "schema_sha256", _sha(self.schema_sha256, "schema_sha256"))
        object.__setattr__(self, "enabled", _bool(self.enabled))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "schema_sha256": self.schema_sha256, "enabled": self.enabled}


def _tool(value: Any) -> ToolInventoryEntry:
    if isinstance(value, ToolInventoryEntry):
        return value
    raw = _entry_dict(value, "tool", _TOOL)
    return ToolInventoryEntry(raw["name"], raw["schema_sha256"], raw["declared_by"], raw["enabled"])


def _server(value: Any) -> MCPServerInventoryEntry:
    if isinstance(value, MCPServerInventoryEntry):
        return value
    raw = _entry_dict(value, "MCP server", _ENTRY)
    return MCPServerInventoryEntry(raw["name"], raw["schema_sha256"], raw["enabled"])


def _observed(value: Any) -> ObservedInventoryEntry:
    if isinstance(value, ObservedInventoryEntry):
        return value
    raw = _entry_dict(value, "observed entry", _ENTRY)
    return ObservedInventoryEntry(raw["name"], raw["schema_sha256"], raw["enabled"])


def _sort_entries(values: Sequence[Any], field: str) -> tuple[Any, ...]:
    entries = tuple(values)
    names = tuple(item.name for item in entries)
    if len(set(names)) != len(names):
        raise InventoryValidationError(f"{field} contains duplicate names")
    return tuple(sorted(entries, key=lambda item: item.name))


def _declared_projection(schema_version: int, tools: Sequence[ToolInventoryEntry],
                         servers: Sequence[MCPServerInventoryEntry]) -> dict[str, object]:
    return {"schema_version": schema_version,
            "tools": [item.to_dict() for item in tools],
            "mcp_servers": [item.to_dict() for item in servers]}


def _observed_projection(candidate_sha256: str, tools: Sequence[ObservedInventoryEntry],
                         servers: Sequence[ObservedInventoryEntry]) -> dict[str, object]:
    return {"candidate_sha256": candidate_sha256,
            "tools": [item.to_dict() for item in tools],
            "mcp_servers": [item.to_dict() for item in servers]}


def _verify_hash(actual: Any, expected: str, field: str) -> None:
    if _sha(actual, field) != expected:
        raise InventoryValidationError(f"{field} does not match its projection")


@dataclass(frozen=True, slots=True)
class DeclaredInventory:
    schema_version: int
    tools: tuple[ToolInventoryEntry, ...]
    mcp_servers: tuple[MCPServerInventoryEntry, ...]
    declared_inventory_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise InventoryValidationError("schema_version must be 1")
        tools = _sort_entries(tuple(_tool(item) for item in self.tools), "tools")
        servers = _sort_entries(tuple(_server(item) for item in self.mcp_servers), "mcp_servers")
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "mcp_servers", servers)
        _verify_hash(self.declared_inventory_sha256,
                     canonical_sha256(_declared_projection(self.schema_version, tools, servers)),
                     "declared_inventory_sha256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeclaredInventory":
        raw = _object(value, "declared inventory", _DECLARED)
        return cls(raw["schema_version"],
                   tuple(_tool(item) for item in _array(raw["tools"], "tools")),
                   tuple(_server(item) for item in _array(raw["mcp_servers"], "mcp_servers")),
                   raw["declared_inventory_sha256"])

    def to_dict(self) -> dict[str, object]:
        return {**_declared_projection(self.schema_version, self.tools, self.mcp_servers),
                "declared_inventory_sha256": self.declared_inventory_sha256}


@dataclass(frozen=True, slots=True)
class InventoryDrift:
    unknown_names: tuple[str, ...] = ()
    missing_names: tuple[str, ...] = ()
    schema_drift_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("unknown_names", "missing_names", "schema_drift_names"):
            object.__setattr__(self, field, _sorted_names(getattr(self, field), field))

    @property
    def exact(self) -> bool:
        return not (self.unknown_names or self.missing_names or self.schema_drift_names)

    def to_dict(self) -> dict[str, list[str]]:
        return {field: list(getattr(self, field)) for field in
                ("unknown_names", "missing_names", "schema_drift_names")}


@dataclass(frozen=True, slots=True)
class ObservedInventory:
    candidate_sha256: str
    tools: tuple[ObservedInventoryEntry, ...]
    mcp_servers: tuple[ObservedInventoryEntry, ...]
    observed_inventory_sha256: str
    unknown_names: tuple[str, ...] = ()
    missing_names: tuple[str, ...] = ()
    schema_drift_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_sha256", _sha(self.candidate_sha256, "candidate_sha256"))
        object.__setattr__(self, "tools", _sort_entries(tuple(_observed(item) for item in self.tools), "tools"))
        object.__setattr__(self, "mcp_servers", _sort_entries(
            tuple(_observed(item) for item in self.mcp_servers), "mcp_servers"))
        drift = InventoryDrift(self.unknown_names, self.missing_names, self.schema_drift_names)
        for field in ("unknown_names", "missing_names", "schema_drift_names"):
            object.__setattr__(self, field, getattr(drift, field))
        _verify_hash(self.observed_inventory_sha256,
                     canonical_sha256(_observed_projection(self.candidate_sha256, self.tools, self.mcp_servers)),
                     "observed_inventory_sha256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *,
                     declared: DeclaredInventory | Mapping[str, Any] | None = None) -> "ObservedInventory":
        raw = _object(value, "observed inventory", _OBSERVED)
        result = cls(raw["candidate_sha256"],
                     tuple(_observed(item) for item in _array(raw["tools"], "tools")),
                     tuple(_observed(item) for item in _array(raw["mcp_servers"], "mcp_servers")),
                     raw["observed_inventory_sha256"],
                     _sorted_names(raw["unknown_names"], "unknown_names"),
                     _sorted_names(raw["missing_names"], "missing_names"),
                     _sorted_names(raw["schema_drift_names"], "schema_drift_names"))
        expected = derive_inventory_drift(declared, result) if declared is not None else None
        actual = InventoryDrift(result.unknown_names, result.missing_names, result.schema_drift_names)
        if expected is not None and actual != expected:
            raise InventoryValidationError("observed inventory drift does not match declared inventory")
        return result

    def to_dict(self) -> dict[str, object]:
        return {**_observed_projection(self.candidate_sha256, self.tools, self.mcp_servers),
                "observed_inventory_sha256": self.observed_inventory_sha256,
                **InventoryDrift(self.unknown_names, self.missing_names,
                                 self.schema_drift_names).to_dict()}


def build_declared_inventory(tools: Sequence[ToolInventoryEntry | Mapping[str, Any]],
                             mcp_servers: Sequence[MCPServerInventoryEntry | Mapping[str, Any]] = (),
                             *, schema_version: int = 1) -> DeclaredInventory:
    if type(schema_version) is not int or schema_version != 1:
        raise InventoryValidationError("schema_version must be 1")
    normalized_tools = _sort_entries(tuple(_tool(item) for item in _array(tools, "tools")), "tools")
    normalized_servers = _sort_entries(tuple(_server(item) for item in _array(mcp_servers, "mcp_servers")), "mcp_servers")
    digest = canonical_sha256(_declared_projection(schema_version, normalized_tools, normalized_servers))
    return DeclaredInventory(schema_version, normalized_tools, normalized_servers, digest)


def validate_declared_inventory(value: DeclaredInventory | Mapping[str, Any]) -> DeclaredInventory:
    return value if isinstance(value, DeclaredInventory) else DeclaredInventory.from_mapping(value)


def compute_declared_inventory_sha256(value: DeclaredInventory | Mapping[str, Any]) -> str:
    inventory = validate_declared_inventory(value)
    return canonical_sha256(_declared_projection(inventory.schema_version, inventory.tools, inventory.mcp_servers))


def _index(tools: Sequence[Any], servers: Sequence[Any]) -> dict[tuple[str, str], Any]:
    return {**{("tool", item.name): item for item in tools},
            **{("mcp_server", item.name): item for item in servers}}


def derive_inventory_drift(declared: DeclaredInventory | Mapping[str, Any],
                           observed: ObservedInventory | Mapping[str, Any]) -> InventoryDrift:
    left = validate_declared_inventory(declared)
    right = observed if isinstance(observed, ObservedInventory) else ObservedInventory.from_mapping(observed)
    expected, actual = _index(left.tools, left.mcp_servers), _index(right.tools, right.mcp_servers)
    unknown = sorted(name for kind, name in actual if (kind, name) not in expected)
    missing = sorted(name for kind, name in expected if (kind, name) not in actual)
    drift = sorted(key[1] for key, item in expected.items()
                   if key in actual and (item.schema_sha256 != actual[key].schema_sha256
                                         or item.enabled != actual[key].enabled))
    return InventoryDrift(tuple(unknown), tuple(missing), tuple(drift))


def build_observed_inventory(candidate_sha256: str,
                             tools: Sequence[ObservedInventoryEntry | Mapping[str, Any]],
                             mcp_servers: Sequence[ObservedInventoryEntry | Mapping[str, Any]] = (),
                             *, declared: DeclaredInventory | Mapping[str, Any] | None = None) -> ObservedInventory:
    candidate = _sha(candidate_sha256, "candidate_sha256")
    normalized_tools = _sort_entries(tuple(_observed(item) for item in _array(tools, "tools")), "tools")
    normalized_servers = _sort_entries(tuple(_observed(item) for item in _array(mcp_servers, "mcp_servers")), "mcp_servers")
    digest = canonical_sha256(_observed_projection(candidate, normalized_tools, normalized_servers))
    provisional = ObservedInventory(candidate, normalized_tools, normalized_servers, digest)
    drift = InventoryDrift() if declared is None else derive_inventory_drift(declared, provisional)
    return ObservedInventory(candidate, normalized_tools, normalized_servers, digest,
                             drift.unknown_names, drift.missing_names, drift.schema_drift_names)


def validate_observed_inventory(value: ObservedInventory | Mapping[str, Any], *,
                                declared: DeclaredInventory | Mapping[str, Any] | None = None) -> ObservedInventory:
    if isinstance(value, ObservedInventory) and declared is None:
        return value
    result = value if isinstance(value, ObservedInventory) else ObservedInventory.from_mapping(value, declared=declared)
    if declared is not None and InventoryDrift(result.unknown_names, result.missing_names,
                                               result.schema_drift_names) != derive_inventory_drift(declared, result):
        raise InventoryValidationError("observed inventory drift does not match declared inventory")
    return result


def compute_observed_inventory_sha256(value: ObservedInventory | Mapping[str, Any]) -> str:
    inventory = validate_observed_inventory(value)
    return canonical_sha256(_observed_projection(inventory.candidate_sha256, inventory.tools, inventory.mcp_servers))


def inventory_exact(declared: DeclaredInventory | Mapping[str, Any],
                    observed: ObservedInventory | Mapping[str, Any]) -> bool:
    return derive_inventory_drift(declared, observed).exact


def compute_schema_sha256(schema: Any) -> str:
    try:
        return canonical_sha256(schema)
    except CanonicalizationError as exc:
        raise InventoryValidationError("tool schema is not canonical JSON") from exc


def build_declared_inventory_from_tool_schemas(
    tool_schemas: Sequence[Any], *, declared_by: str = "host", enabled: bool = True,
    mcp_servers: Sequence[MCPServerInventoryEntry | Mapping[str, Any]] = (),
) -> DeclaredInventory:
    if not isinstance(declared_by, str) or declared_by not in _SIDES:
        raise InventoryValidationError("declared_by must be host or plugin")
    active = _bool(enabled)
    try:
        from ..tool_bridge import normalize_tool_schemas
        definitions = normalize_tool_schemas(tool_schemas)
    except Exception as exc:
        raise InventoryValidationError("public tool schema normalization failed") from exc
    tools = tuple(ToolInventoryEntry(item.name, compute_schema_sha256(item.input_schema), declared_by, active)
                  for item in definitions)
    return build_declared_inventory(tools, mcp_servers)


__all__ = [
    "DeclaredInventory", "InventoryDrift", "InventoryValidationError",
    "MCPServerInventoryEntry", "ObservedInventory", "ObservedInventoryEntry",
    "ToolInventoryEntry", "build_declared_inventory",
    "build_declared_inventory_from_tool_schemas", "build_observed_inventory",
    "compute_declared_inventory_sha256", "compute_observed_inventory_sha256",
    "compute_schema_sha256", "derive_inventory_drift", "inventory_exact",
    "validate_declared_inventory", "validate_observed_inventory",
]

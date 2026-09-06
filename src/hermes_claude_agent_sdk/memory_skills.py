"""Safe, deterministic references to host-owned memory and skill tools.

The Hermes host owns tool registration and invocation.  This module deliberately
does not import that host implementation: it accepts a public tool-schema
snapshot, keeps only the read-side names that this runtime can describe, and
returns opaque references plus stable hashes.  A reference is metadata, never a
callable or a copy of the host schema.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

MemorySkillKind = Literal["memory", "skill"]

# These are intentionally a small allowlist.  In particular, ``skill_manage``
# is not included because the standalone surface only advertises read-side
# skill references; the host remains the owner of any mutating tool.
MEMORY_TOOL_NAMES = frozenset({"memory", "session_search"})
SKILL_TOOL_NAMES = frozenset({"skill_view", "skills_list"})
READ_SIDE_TOOL_NAMES = MEMORY_TOOL_NAMES | SKILL_TOOL_NAMES


def _canonical_value(value: Any) -> Any:
    """Return JSON-safe structure for hashing without retaining object values.

    Tool schemas are normally JSON-like mappings.  Unknown Python objects are
    represented by their type only, rather than by ``repr`` (which can contain
    a credential, a path, or another host-private value).
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"__float__": str(value)}
    if isinstance(value, Mapping):
        pairs: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                pairs.append((f"<key:{type(key).__name__}>", {"__type__": type(item).__name__}))
                continue
            pairs.append((key, _canonical_value(item)))
        return dict(sorted(pairs, key=lambda pair: pair[0]))
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        values = [_canonical_value(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if isinstance(value, bytes):
        return {"__bytes_length__": len(value)}
    return {"__type__": type(value).__name__}


def stable_tool_schema_hash(schema: Any) -> str:
    """Hash a schema deterministically without exposing its raw contents."""

    encoded = json.dumps(
        _canonical_value(schema),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_name(schema: Any) -> str | None:
    if not isinstance(schema, Mapping):
        return None
    raw_name = schema.get("name")
    if not isinstance(raw_name, str):
        function = schema.get("function")
        if isinstance(function, Mapping):
            raw_name = function.get("name")
    if not isinstance(raw_name, str):
        return None

    # Host adapters may namespace MCP tools.  Only the final segment is
    # considered, and the returned name is always the known bare name.
    candidate = raw_name.strip().lower()
    candidate = candidate.rsplit("__", 1)[-1]
    return candidate if candidate in READ_SIDE_TOOL_NAMES else None


@dataclass(frozen=True)
class ToolSchemaReference:
    """Opaque metadata for one host-owned read-side tool schema."""

    name: str
    kind: MemorySkillKind
    schema_hash: str


@dataclass(frozen=True)
class MemorySkillReferences:
    """Stable references used to shape an SDK request.

    ``references`` contains names and hashes only.  It intentionally contains
    no schema mapping, callback, executor, or configuration object.
    """

    references: tuple[ToolSchemaReference, ...] = ()
    schema_hash: str = ""

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(
                self.references,
                key=lambda ref: (ref.kind, ref.name, ref.schema_hash),
            )
        )
        object.__setattr__(self, "references", ordered)
        if not self.schema_hash:
            payload = [
                {
                    "kind": ref.kind,
                    "name": ref.name,
                    "schema_hash": ref.schema_hash,
                }
                for ref in ordered
            ]
            object.__setattr__(self, "schema_hash", stable_tool_schema_hash(payload))

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Return canonical names in deterministic display order."""

        return tuple(ref.name for ref in self.references)

    @property
    def memory_tool_names(self) -> tuple[str, ...]:
        return tuple(ref.name for ref in self.references if ref.kind == "memory")

    @property
    def skill_tool_names(self) -> tuple[str, ...]:
        return tuple(ref.name for ref in self.references if ref.kind == "skill")

    def prompt_guidance(self) -> str:
        """Render short guidance for tools proven present by this snapshot."""

        parts: list[str] = []
        if "memory" in self.memory_tool_names:
            parts.append("Use the host-provided `memory` tool for durable facts.")
        if "session_search" in self.memory_tool_names:
            parts.append(
                "Use the host-provided `session_search` tool for relevant cross-session context."
            )
        skill_names = [name for name in self.skill_tool_names if name in SKILL_TOOL_NAMES]
        if skill_names:
            rendered = " and ".join(f"`{name}`" for name in skill_names)
            parts.append(f"Use the host-provided {rendered} tools to inspect skills.")
        return "\n".join(parts)


def build_memory_skill_references(
    tool_schemas: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> MemorySkillReferences:
    """Build opaque references from a public, immutable tool-schema snapshot.

    Unknown names and malformed values are ignored.  Duplicate names resolve
    to the lexicographically smallest schema hash, making the result stable
    even if the host presents duplicate schemas in a different order.
    """

    if tool_schemas is None:
        schemas: Sequence[Any] = ()
    elif isinstance(tool_schemas, Mapping):
        schemas = (tool_schemas,)
    elif isinstance(tool_schemas, Sequence) and not isinstance(
        tool_schemas, (str, bytes, bytearray)
    ):
        schemas = tool_schemas
    else:
        schemas = ()

    selected: dict[str, ToolSchemaReference] = {}
    for schema in schemas:
        name = _schema_name(schema)
        if name is None:
            continue
        kind: MemorySkillKind = "memory" if name in MEMORY_TOOL_NAMES else "skill"
        reference = ToolSchemaReference(
            name=name,
            kind=kind,
            schema_hash=stable_tool_schema_hash(schema),
        )
        previous = selected.get(name)
        if previous is None or reference.schema_hash < previous.schema_hash:
            selected[name] = reference

    return MemorySkillReferences(tuple(selected.values()))


# Short aliases are useful to host adapters while keeping the descriptive API
# above available to callers and tests.
build_tool_references = build_memory_skill_references
stable_schema_hash = stable_tool_schema_hash


__all__ = [
    "MEMORY_TOOL_NAMES",
    "READ_SIDE_TOOL_NAMES",
    "SKILL_TOOL_NAMES",
    "MemorySkillReferences",
    "ToolSchemaReference",
    "build_memory_skill_references",
    "build_tool_references",
    "stable_schema_hash",
    "stable_tool_schema_hash",
]

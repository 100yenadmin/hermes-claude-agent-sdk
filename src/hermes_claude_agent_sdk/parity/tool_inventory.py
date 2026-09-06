"""Repo-owned tool surface used by the isolated v3 evaluation profile.

The declared surface is deliberately small.  It contains only the harmless
approval probe and the four synthetic native-benchmark tools.  The observed
surface is produced by the real :class:`HostToolBridge` normalization path so
schema drift in the adapter cannot be hidden by copying the declaration into
both halves of an inventory packet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hermes_claude_agent_sdk.tool_bridge import HostToolBridge

from .hashing import json_compatible
from .native_sandbox import tool_schemas


APPROVAL_TOOL_NAME = "parity_harmless_tool"
APPROVAL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": APPROVAL_TOOL_NAME,
        "description": "Record one in-memory parity marker after approval",
        "parameters": {
            "type": "object",
            "properties": {
                "marker": {"type": "string", "enum": ["feature-parity-v3"]},
            },
            "required": ["marker"],
            "additionalProperties": False,
        },
    },
}


class _InventoryHost:
    """Non-executing host used only to normalize public tool definitions."""

    def cancellation_requested(self) -> bool:
        return False

    async def execute_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        del name, arguments
        raise RuntimeError("inventory capture never executes tools")


def declared_tool_schemas() -> tuple[dict[str, Any], ...]:
    """Return fresh JSON-compatible declarations for the complete RC surface."""

    values = (APPROVAL_TOOL_SCHEMA, *tool_schemas(("read", "write", "exec", "cron")))
    return tuple(json_compatible(value) for value in values)


def inventory_rows_from_schemas(
    schemas: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Project public function schemas into the portable inventory shape."""

    rows: list[dict[str, Any]] = []
    for schema in schemas:
        function = schema["function"]
        rows.append(
            {
                "name": function["name"],
                "input_schema": json_compatible(function["parameters"]),
            }
        )
    return tuple(sorted(rows, key=lambda item: item["name"]))


def capture_observed_tool_rows() -> tuple[dict[str, Any], ...]:
    """Observe the declared surface after canonical host-bridge normalization."""

    bridge = HostToolBridge(_InventoryHost(), declared_tool_schemas())
    rows = (
        {
            "name": definition.name,
            "input_schema": json_compatible(definition.input_schema),
        }
        for definition in bridge.tool_definitions
    )
    return tuple(sorted(rows, key=lambda item: item["name"]))


__all__ = [
    "APPROVAL_TOOL_NAME",
    "APPROVAL_TOOL_SCHEMA",
    "capture_observed_tool_rows",
    "declared_tool_schemas",
    "inventory_rows_from_schemas",
]

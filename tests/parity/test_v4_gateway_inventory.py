from __future__ import annotations

import json

import pytest

from hermes_claude_agent_sdk.parity.v4_gateway_inventory import (
    V4GatewayInventoryViolation,
    build_v4_gateway_inventory,
    load_v4_gateway_inventory,
)


PROFILE_SHA = "a" * 64
HOST_SHA = "b" * 40


def _schema(name: str, kind: str = "string") -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": kind}},
            },
        },
    }


def _inventory():
    return build_v4_gateway_inventory(
        profile_id="fable-v3-isolated",
        profile_sha256=PROFILE_SHA,
        host_sha=HOST_SHA,
        delivered_schemas=[
            _schema("delegate_task"),
            _schema("read_file"),
            _schema("tool_call"),
            _schema("tool_describe"),
            _schema("tool_search"),
        ],
        executable_schemas=[_schema("delegate_task"), _schema("read_file")],
    )


def test_inventory_binds_two_exact_surfaces_and_round_trips(tmp_path):
    inventory = _inventory()
    assert inventory.delivered_names == {
        "delegate_task",
        "read_file",
        "tool_call",
        "tool_describe",
        "tool_search",
    }
    assert inventory.executable_names == {"delegate_task", "read_file"}
    assert inventory.host_event_names == inventory.delivered_names
    assert inventory.mcp_event_names == {
        "mcp__hermes-tools__delegate_task",
        "mcp__hermes-tools__read_file",
        "mcp__hermes-tools__tool_call",
        "mcp__hermes-tools__tool_describe",
        "mcp__hermes-tools__tool_search",
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory.to_dict()), encoding="utf-8")
    loaded = load_v4_gateway_inventory(
        path,
        expected_profile_id="fable-v3-isolated",
        expected_profile_sha256=PROFILE_SHA,
        expected_host_sha=HOST_SHA,
    )
    assert loaded == inventory


def test_inventory_rejects_tampering_native_tools_and_unexplained_surface_gaps():
    document = _inventory().to_dict()
    document["inventory_sha256"] = "c" * 64
    with pytest.raises(V4GatewayInventoryViolation, match="inventory_sha256"):
        load_v4_gateway_inventory(document)

    with pytest.raises(V4GatewayInventoryViolation, match="Claude-native"):
        build_v4_gateway_inventory(
            profile_id="fable-v3-isolated",
            profile_sha256=PROFILE_SHA,
            host_sha=HOST_SHA,
            delivered_schemas=[_schema("Agent")],
            executable_schemas=[_schema("read_file")],
        )

    with pytest.raises(V4GatewayInventoryViolation, match="discovery bridges"):
        build_v4_gateway_inventory(
            profile_id="fable-v3-isolated",
            profile_sha256=PROFILE_SHA,
            host_sha=HOST_SHA,
            delivered_schemas=[_schema("delegate_task")],
            executable_schemas=[_schema("read_file")],
        )


def test_inventory_rejects_schema_drift_between_shared_surfaces():
    with pytest.raises(V4GatewayInventoryViolation, match="schemas differ"):
        build_v4_gateway_inventory(
            profile_id="fable-v3-isolated",
            profile_sha256=PROFILE_SHA,
            host_sha=HOST_SHA,
            delivered_schemas=[_schema("read_file", "string")],
            executable_schemas=[_schema("read_file", "integer")],
        )

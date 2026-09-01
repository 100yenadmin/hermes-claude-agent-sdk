from __future__ import annotations

import copy
import json

import pytest
import yaml

from hermes_claude_agent_sdk.parity.inventory import (
    InventoryViolation,
    capture_tool_inventory,
    load_tool_inventory,
)
from hermes_claude_agent_sdk.parity.tool_inventory import declared_tool_schemas


def _document() -> dict:
    return {
        "schema_version": 1,
        "profile_id": "fable-v3-isolated",
        "profile_hash": "3" * 64,
        "declared_tools": [
            {
                "name": "memory",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "name": "repo_read",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ],
        "observed_tools": [],
    }


def _write(tmp_path, document: dict):
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_inventory_accepts_order_independent_exact_schema_coverage(tmp_path) -> None:
    document = _document()
    document["observed_tools"] = list(reversed(copy.deepcopy(document["declared_tools"])))
    inventory = load_tool_inventory(
        _write(tmp_path, document), expected_profile="fable-v3-isolated"
    )
    assert inventory.tool_count == 2
    assert len(inventory.inventory_hash) == 64
    assert [item["name"] for item in inventory.observed_tools] == ["memory", "repo_read"]


@pytest.mark.parametrize("mutation", ["missing", "unknown", "schema"])
def test_inventory_fails_closed_on_tool_or_schema_drift(tmp_path, mutation: str) -> None:
    document = _document()
    document["observed_tools"] = copy.deepcopy(document["declared_tools"])
    if mutation == "missing":
        document["observed_tools"].pop()
    elif mutation == "unknown":
        document["observed_tools"].append(
            {"name": "escape", "input_schema": {"type": "object"}}
        )
    else:
        document["observed_tools"][0]["input_schema"]["properties"]["query"]["type"] = "number"
    with pytest.raises(InventoryViolation, match="tool inventory drift"):
        load_tool_inventory(_write(tmp_path, document))


def test_inventory_rejects_profile_ambiguity(tmp_path) -> None:
    document = _document()
    document["observed_tools"] = copy.deepcopy(document["declared_tools"])
    with pytest.raises(InventoryViolation, match="requested profile"):
        load_tool_inventory(_write(tmp_path, document), expected_profile="shared-eva")


def test_inventory_rejects_profile_manifest_hash_mismatch(tmp_path) -> None:
    document = _document()
    document["observed_tools"] = copy.deepcopy(document["declared_tools"])
    with pytest.raises(InventoryViolation, match="profile manifest"):
        load_tool_inventory(
            _write(tmp_path, document),
            expected_profile_hash="4" * 64,
        )


def test_capture_observes_complete_surface_through_host_bridge(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "fable-v3-isolated",
                "isolation_kind": "in_process_fixture",
                "persistent": False,
                "shared_state": False,
                "customer_data": False,
                "configuration_hash": "9" * 64,
            }
        ),
        encoding="utf-8",
    )
    document = capture_tool_inventory(
        profile_path,
        expected_profile="fable-v3-isolated",
    )
    assert document["declared_tools"] == document["observed_tools"]
    assert [item["name"] for item in document["observed_tools"]] == [
        "cron",
        "exec",
        "parity_harmless_tool",
        "read",
        "write",
    ]
    assert len(declared_tool_schemas()) == 5

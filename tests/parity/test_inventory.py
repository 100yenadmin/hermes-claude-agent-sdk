from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.canonical import canonical_sha256
from hermes_claude_agent_sdk.parity.inventory import (
    DeclaredInventory,
    InventoryValidationError,
    MCPServerInventoryEntry,
    ObservedInventory,
    ObservedInventoryEntry,
    ToolInventoryEntry,
    build_declared_inventory,
    build_declared_inventory_from_tool_schemas,
    build_observed_inventory,
    compute_declared_inventory_sha256,
    compute_observed_inventory_sha256,
    compute_schema_sha256,
    derive_inventory_drift,
    inventory_exact,
    validate_declared_inventory,
    validate_observed_inventory,
)


_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_CANDIDATE = "e" * 64


def _declared() -> DeclaredInventory:
    return build_declared_inventory(
        [
            ToolInventoryEntry("zeta", _B, "plugin", True),
            ToolInventoryEntry("alpha", _A, "host", True),
        ],
        [MCPServerInventoryEntry("hermes", _C, True)],
    )


def test_declared_inventory_is_sorted_and_hashes_only_declared_projection() -> None:
    inventory = _declared()

    assert [item.name for item in inventory.tools] == ["alpha", "zeta"]
    assert [item.name for item in inventory.mcp_servers] == ["hermes"]
    projection = {
        "schema_version": 1,
        "tools": [item.to_dict() for item in inventory.tools],
        "mcp_servers": [item.to_dict() for item in inventory.mcp_servers],
    }
    assert inventory.declared_inventory_sha256 == canonical_sha256(projection)
    assert compute_declared_inventory_sha256(inventory) == inventory.declared_inventory_sha256
    assert validate_declared_inventory(inventory).to_dict() == inventory.to_dict()


def test_declared_mapping_accepts_unsorted_entries_but_emits_canonical_order() -> None:
    inventory = _declared()
    raw = inventory.to_dict()
    raw["tools"] = list(reversed(raw["tools"]))

    restored = validate_declared_inventory(raw)

    assert [item.name for item in restored.tools] == ["alpha", "zeta"]
    assert restored.declared_inventory_sha256 == inventory.declared_inventory_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duplicate tools", [ToolInventoryEntry("alpha", _A, "host", True), ToolInventoryEntry("alpha", _B, "host", True)]),
        ("duplicate servers", [MCPServerInventoryEntry("hermes", _A, True), MCPServerInventoryEntry("hermes", _B, True)]),
    ],
)
def test_declared_inventory_rejects_duplicate_names(field: str, value: list[Any]) -> None:
    if field == "duplicate tools":
        with pytest.raises(InventoryValidationError, match="duplicate"):
            build_declared_inventory(value)
    else:
        with pytest.raises(InventoryValidationError, match="duplicate"):
            build_declared_inventory((), value)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"name": "alpha", "schema_sha256": "not-a-digest", "declared_by": "host", "enabled": True}, "digest"),
        ({"name": "alpha", "schema_sha256": _A, "declared_by": "operator", "enabled": True}, "declared_by"),
        ({"name": "alpha", "schema_sha256": _A, "declared_by": "host", "enabled": 1}, "boolean"),
        ({"name": "alpha", "schema_sha256": _A, "declared_by": "host", "enabled": True, "extra": 1}, "unexpected"),
    ],
)
def test_declared_inventory_rejects_malformed_tool_metadata(
    entry: dict[str, Any], message: str
) -> None:
    with pytest.raises(InventoryValidationError, match=message):
        build_declared_inventory([entry])


def test_observed_inventory_hash_excludes_derived_drift_lists() -> None:
    declared = _declared()
    observed = build_observed_inventory(
        _CANDIDATE,
        [
            ObservedInventoryEntry("alpha", _D, True),
            ObservedInventoryEntry("omega", _A, True),
        ],
        [],
        declared=declared,
    )

    assert observed.unknown_names == ("omega",)
    assert observed.missing_names == ("hermes", "zeta")
    assert observed.schema_drift_names == ("alpha",)
    projection = {
        "candidate_sha256": _CANDIDATE,
        "tools": [item.to_dict() for item in observed.tools],
        "mcp_servers": [],
    }
    assert observed.observed_inventory_sha256 == canonical_sha256(projection)
    assert compute_observed_inventory_sha256(observed) == observed.observed_inventory_sha256

    raw = observed.to_dict()
    raw["unknown_names"] = []
    raw["missing_names"] = []
    raw["schema_drift_names"] = []
    without_declared = validate_observed_inventory(raw)
    assert without_declared.observed_inventory_sha256 == observed.observed_inventory_sha256
    with pytest.raises(InventoryValidationError, match="drift"):
        validate_observed_inventory(raw, declared=declared)


def test_observed_inventory_derives_enabled_and_server_schema_drift() -> None:
    declared = _declared()
    observed = build_observed_inventory(
        _CANDIDATE,
        [ObservedInventoryEntry("alpha", _A, True)],
        [{"name": "hermes", "schema_sha256": _C, "enabled": False}],
        declared=declared,
    )

    assert observed.unknown_names == ()
    assert observed.missing_names == ("zeta",)
    assert observed.schema_drift_names == ("hermes",)
    assert not inventory_exact(declared, observed)
    assert derive_inventory_drift(declared, observed).schema_drift_names == ("hermes",)


def test_observed_exact_match_requires_names_digests_and_enabled_state() -> None:
    declared = _declared()
    observed = build_observed_inventory(
        _CANDIDATE,
        [
            {"name": "zeta", "schema_sha256": _B, "enabled": True},
            {"name": "alpha", "schema_sha256": _A, "enabled": True},
        ],
        [{"name": "hermes", "schema_sha256": _C, "enabled": True}],
        declared=declared,
    )

    assert inventory_exact(declared, observed) is True


def test_observed_inventory_rejects_unsorted_or_duplicate_derived_names() -> None:
    observed = build_observed_inventory(_CANDIDATE, [])
    for field, value in (
        ("unknown_names", ["zeta", "alpha"]),
        ("missing_names", ["alpha", "alpha"]),
    ):
        raw = observed.to_dict()
        raw[field] = value
        with pytest.raises(InventoryValidationError, match=field):
            validate_observed_inventory(raw)


def test_observed_inventory_rejects_malformed_entries_and_hash_tampering() -> None:
    malformed = (
        {"name": "alpha", "schema_sha256": "not-a-digest", "enabled": True},
        {"name": "alpha", "schema_sha256": _A, "enabled": 1},
        {"name": "alpha", "schema_sha256": _A, "enabled": True, "extra": 1},
    )
    for entry in malformed:
        with pytest.raises(InventoryValidationError):
            build_observed_inventory(_CANDIDATE, [entry])
    with pytest.raises(InventoryValidationError, match="duplicate"):
        build_observed_inventory(_CANDIDATE, [
            {"name": "alpha", "schema_sha256": _A, "enabled": True},
            {"name": "alpha", "schema_sha256": _A, "enabled": True},
        ])

    observed = build_observed_inventory(_CANDIDATE, [])
    raw = observed.to_dict()
    raw["observed_inventory_sha256"] = _A
    with pytest.raises(InventoryValidationError, match="projection"):
        validate_observed_inventory(raw)


def test_public_schema_normalization_is_lazy_and_metadata_only() -> None:
    schema = {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Synthetic terminal metadata",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }

    inventory = build_declared_inventory_from_tool_schemas([schema])

    assert inventory.tools[0].name == "terminal"
    assert inventory.tools[0].declared_by == "host"
    assert inventory.tools[0].enabled is True
    assert inventory.tools[0].schema_sha256 == compute_schema_sha256(
        schema["function"]["parameters"]
    )


def test_parity_inventory_import_is_host_sdk_and_auth_lazy() -> None:
    source_root = Path(__file__).parents[2] / "src"
    probe = (
        "import sys\n"
        "import hermes_claude_agent_sdk.parity.inventory\n"
        "assert 'agent' not in sys.modules\n"
        "assert 'claude_agent_sdk' not in sys.modules\n"
        "assert 'hermes_claude_agent_sdk.auth' not in sys.modules\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        env={"PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_declared_and_observed_hashes_are_independent() -> None:
    declared = _declared()
    changed_declared = build_declared_inventory(
        [
            ToolInventoryEntry("alpha", _A, "host", False),
            ToolInventoryEntry("zeta", _B, "plugin", True),
        ],
        [MCPServerInventoryEntry("hermes", _C, True)],
    )
    observed = build_observed_inventory(
        _CANDIDATE,
        [{"name": "alpha", "schema_sha256": _A, "enabled": True}],
    )
    changed_candidate = build_observed_inventory(
        hashlib.sha256(b"different synthetic candidate").hexdigest(),
        [{"name": "alpha", "schema_sha256": _A, "enabled": True}],
    )

    assert changed_declared.declared_inventory_sha256 != declared.declared_inventory_sha256
    assert changed_candidate.observed_inventory_sha256 != observed.observed_inventory_sha256

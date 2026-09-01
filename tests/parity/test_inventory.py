from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from hermes_claude_agent_sdk.parity.canonical import canonical_sha256
from hermes_claude_agent_sdk.parity.inventory import (
    DeclaredInventory,
    InventoryViolation,
    InventoryValidationError,
    MCPServerInventoryEntry,
    ObservedInventory,
    ObservedInventoryEntry,
    ToolInventoryEntry,
    build_declared_inventory,
    build_declared_inventory_from_runtime_request,
    build_declared_inventory_from_tool_schemas,
    build_observed_inventory,
    capture_tool_inventory,
    compute_declared_inventory_sha256,
    compute_observed_inventory_sha256,
    compute_schema_sha256,
    derive_inventory_drift,
    inventory_exact,
    load_tool_inventory,
    validate_declared_inventory,
    validate_observed_inventory,
)
from hermes_claude_agent_sdk.parity.tool_inventory import declared_tool_schemas


_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_CANDIDATE = "e" * 64


def _runtime_inventory_request(
    *,
    schema_version: int = 1,
    surface: object = "delivered_request",
    tool_names: tuple[str, ...] | None = None,
    tool_digest_overrides: dict[str, str] | None = None,
    tool_enabled: dict[str, bool] | None = None,
    server_names: tuple[str, ...] | None = None,
    server_digest_overrides: dict[str, str] | None = None,
    server_enabled: dict[str, bool] | None = None,
) -> SimpleNamespace:
    schemas = (
        {
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
        },
        {
            "type": "function",
            "function": {
                "name": "mcp__files__read",
                "description": "Synthetic file metadata",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
    )
    digests = {
        item["function"]["name"]: compute_schema_sha256(item["function"]["parameters"])
        for item in schemas
    }
    digests.update(tool_digest_overrides or {})
    ownership = {"terminal": "host", "mcp__files__read": "plugin"}
    selected_tools = tool_names or tuple(sorted(digests))
    delivered = tuple(
        SimpleNamespace(
            name=name,
            schema_sha256=digests.get(name, _D),
            declared_by=ownership.get(name, "host"),
            enabled=(tool_enabled or {}).get(name, True),
        )
        for name in sorted(selected_tools)
    )
    expected_server_digest = canonical_sha256(
        [
            {
                "name": "mcp__files__read",
                "schema_sha256": digests["mcp__files__read"],
                "enabled": True,
            }
        ]
    )
    server_digests = {"files": expected_server_digest}
    server_digests.update(server_digest_overrides or {})
    selected_servers = server_names if server_names is not None else ("files",)
    servers = tuple(
        SimpleNamespace(
            name=name,
            schema_sha256=server_digests.get(name, _C),
            enabled=(server_enabled or {}).get(name, True),
        )
        for name in sorted(selected_servers)
    )
    inventory = SimpleNamespace(
        schema_version=schema_version,
        surface=SimpleNamespace(value=surface) if isinstance(surface, str) else surface,
        tools=delivered,
        mcp_servers=servers,
    )
    return SimpleNamespace(tool_schemas=schemas, tool_inventory=inventory)


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
        "tools": [item.to_dict() for item in inventory.tools],
        "mcp_servers": [item.to_dict() for item in inventory.mcp_servers],
    }
    assert inventory.declared_inventory_sha256 == canonical_sha256(projection)
    assert inventory.declared_inventory_sha256 != canonical_sha256(
        {"schema_version": 1, **projection}
    )
    assert inventory.to_dict()["schema_version"] == 1
    assert compute_declared_inventory_sha256(inventory) == inventory.declared_inventory_sha256
    assert validate_declared_inventory(inventory).to_dict() == inventory.to_dict()


def test_declared_builder_sorts_but_mapping_rejects_unsorted_entries() -> None:
    inventory = build_declared_inventory(
        [ToolInventoryEntry("zeta", _B, "plugin", True), ToolInventoryEntry("alpha", _A, "host", True)],
        [MCPServerInventoryEntry("zulu", _D, True), MCPServerInventoryEntry("hermes", _C, True)],
    )
    assert [item.name for item in inventory.tools] == ["alpha", "zeta"]
    assert [item.name for item in inventory.mcp_servers] == ["hermes", "zulu"]
    for field in ("tools", "mcp_servers"):
        raw = inventory.to_dict()
        raw[field] = list(reversed(raw[field]))
        with pytest.raises(InventoryValidationError, match=field):
            validate_declared_inventory(raw)


def test_observed_builder_sorts_but_mapping_rejects_unsorted_entries() -> None:
    inventory = build_observed_inventory(
        _CANDIDATE,
        [{"name": "zeta", "schema_sha256": _B, "enabled": True},
         {"name": "alpha", "schema_sha256": _A, "enabled": True}],
        [{"name": "zulu", "schema_sha256": _D, "enabled": True},
         {"name": "hermes", "schema_sha256": _C, "enabled": True}],
    )
    assert [item.name for item in inventory.tools] == ["alpha", "zeta"]
    assert [item.name for item in inventory.mcp_servers] == ["hermes", "zulu"]
    for field in ("tools", "mcp_servers"):
        raw = inventory.to_dict()
        raw[field] = list(reversed(raw[field]))
        with pytest.raises(InventoryValidationError, match=field):
            validate_observed_inventory(raw)


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

    assert observed.unknown_names == ("tool:omega",)
    assert observed.missing_names == ("mcp_server:hermes", "tool:zeta")
    assert observed.schema_drift_names == ("tool:alpha",)
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
    assert observed.missing_names == ("tool:zeta",)
    assert observed.schema_drift_names == ("mcp_server:hermes",)
    assert not inventory_exact(declared, observed)
    assert derive_inventory_drift(declared, observed).schema_drift_names == (
        "mcp_server:hermes",
    )


def test_inventory_drift_keeps_tool_and_mcp_server_namespaces_distinct() -> None:
    declared = build_declared_inventory(
        [ToolInventoryEntry("shared", _A, "host", True)],
        [MCPServerInventoryEntry("shared", _B, True)],
    )

    observed = build_observed_inventory(_CANDIDATE, (), (), declared=declared)

    assert observed.missing_names == ("mcp_server:shared", "tool:shared")


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


def test_runtime_request_adapter_preserves_verified_ownership_and_mcp_projection() -> None:
    request = _runtime_inventory_request()

    inventory = build_declared_inventory_from_runtime_request(request)

    assert [(item.name, item.declared_by, item.enabled) for item in inventory.tools] == [
        ("mcp__files__read", "plugin", True),
        ("terminal", "host", True),
    ]
    assert [item.name for item in inventory.mcp_servers] == ["files"]
    expected_tools = {
        item["function"]["name"]: compute_schema_sha256(item["function"]["parameters"])
        for item in request.tool_schemas
    }
    assert {item.name: item.schema_sha256 for item in inventory.tools} == expected_tools
    assert inventory.mcp_servers[0].schema_sha256 == canonical_sha256(
        [
            {
                "name": "mcp__files__read",
                "schema_sha256": expected_tools["mcp__files__read"],
                "enabled": True,
            }
        ]
    )


@pytest.mark.parametrize(
    ("runtime_request", "message"),
    [
        (SimpleNamespace(tool_schemas=(), tool_inventory=None), "tool_inventory"),
        (_runtime_inventory_request(schema_version=2), "schema_version"),
        (_runtime_inventory_request(surface="source_manifest"), "surface"),
        (
            _runtime_inventory_request(tool_enabled={"terminal": False}),
            "delivered tool entries must be enabled",
        ),
        (
            _runtime_inventory_request(server_enabled={"files": False}),
            "delivered MCP server entries must be enabled",
        ),
    ],
)
def test_runtime_request_adapter_rejects_absent_or_incompatible_metadata(
    runtime_request: object, message: str
) -> None:
    with pytest.raises(InventoryValidationError, match=message):
        build_declared_inventory_from_runtime_request(runtime_request)


@pytest.mark.parametrize(
    ("runtime_request", "message"),
    [
        (
            _runtime_inventory_request(tool_names=("terminal",)),
            "tool names",
        ),
        (
            _runtime_inventory_request(tool_names=("terminal", "mcp__files__read", "extra")),
            "tool names",
        ),
        (
            _runtime_inventory_request(tool_digest_overrides={"terminal": _D}),
            "schema_sha256",
        ),
        (
            _runtime_inventory_request(server_names=()),
            "MCP server names",
        ),
        (
            _runtime_inventory_request(server_names=("files", "other")),
            "MCP server names",
        ),
        (
            _runtime_inventory_request(server_digest_overrides={"files": _D}),
            "MCP server schema_sha256",
        ),
    ],
)
def test_runtime_request_adapter_rejects_tool_or_mcp_projection_drift(
    runtime_request: object, message: str
) -> None:
    with pytest.raises(InventoryValidationError, match=message):
        build_declared_inventory_from_runtime_request(runtime_request)


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

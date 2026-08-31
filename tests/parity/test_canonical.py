from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.canonical import (
    SDK_EVENT_CODES,
    TRACE_REGISTRY,
    CanonicalizationError,
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    hash_tool_call_sequence,
    load_json,
)


_SCHEMA_SHA256 = "a" * 64
_REQUEST_SHA256 = "b" * 64

_EXPECTED_TRACE_REGISTRY = {
    "registration.accepted": ("runner", "preflight"),
    "selection.accepted": ("runner", "preflight"),
    "preflight.pass": ("plugin", "preflight"),
    "preflight.fail": ("plugin", "preflight"),
    "approval.requested": ("host", "before_output"),
    "approval.granted": ("host", "before_output"),
    "approval.denied": ("host", "before_output"),
    "approval.late_rejected": ("host", "before_output"),
    "tool.requested": ("plugin", "side_effects"),
    "tool.executed": ("plugin", "side_effects"),
    "tool.denied": ("plugin", "side_effects"),
    "tool.cancelled": ("plugin", "side_effects"),
    "tool.failed": ("plugin", "side_effects"),
    "tool.recovered": ("plugin", "side_effects"),
    "host.execute_tool": ("host", "side_effects"),
    "host.execute_tool_failed": ("host", "side_effects"),
    "state.bound": ("plugin", "lifecycle"),
    "state.invalid": ("plugin", "lifecycle"),
    "resume.supplied": ("plugin", "lifecycle"),
    "resume.accepted": ("plugin", "lifecycle"),
    "resume.rejected": ("plugin", "lifecycle"),
    "session.opened": ("plugin", "lifecycle"),
    "session.restarted": ("plugin", "lifecycle"),
    "session.isolated": ("plugin", "lifecycle"),
    "compaction.started": ("plugin", "lifecycle"),
    "compaction.completed": ("plugin", "lifecycle"),
    "compaction.failed": ("plugin", "lifecycle"),
    "compaction.watchdog": ("plugin", "lifecycle"),
    "usage.included": ("plugin", "lifecycle"),
    "usage.blocked": ("plugin", "lifecycle"),
    "usage.unknown": ("plugin", "lifecycle"),
    "sdk.precompact": ("sdk", "lifecycle"),
    "sdk.compact_boundary": ("sdk", "lifecycle"),
    "sdk.result": ("sdk", "lifecycle"),
    "sdk.tool_use": ("sdk", "side_effects"),
    "sdk.tool_result": ("sdk", "side_effects"),
    "sdk.resume": ("sdk", "lifecycle"),
    "sdk.query": ("sdk", "lifecycle"),
    "path.positive.begin": ("runner", "lifecycle"),
    "path.positive.end": ("runner", "lifecycle"),
    "path.denial.begin": ("runner", "lifecycle"),
    "path.denial.end": ("runner", "lifecycle"),
    "path.recovery.begin": ("runner", "lifecycle"),
    "path.recovery.end": ("runner", "lifecycle"),
    "terminal.complete": ("plugin", "terminal"),
    "terminal.cancelled": ("plugin", "terminal"),
    "terminal.failed": ("plugin", "terminal"),
    "recovery.started": ("host", "side_effects"),
    "recovery.completed": ("host", "side_effects"),
    "recovery.failed": ("host", "side_effects"),
    "inventory.exact": ("runner", "preflight"),
    "inventory.drift": ("runner", "preflight"),
    "package.imported": ("runner", "lifecycle"),
    "package.uninstalled": ("runner", "lifecycle"),
    "package.reinstalled": ("runner", "lifecycle"),
    "ledger.covered": ("runner", "lifecycle"),
    "ledger.equivalent": ("runner", "lifecycle"),
    "ledger.upgrade_required": ("runner", "lifecycle"),
    "ledger.not_applicable": ("runner", "lifecycle"),
}


def _tool_call(
    ordinal: int,
    *,
    name: str = "terminal",
    outcome: str = "executed",
    schema_sha256: str = _SCHEMA_SHA256,
    request_id_sha256: str = _REQUEST_SHA256,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "name": name,
        "schema_sha256": schema_sha256,
        "outcome": outcome,
        "request_id_sha256": request_id_sha256,
    }


def test_canonical_json_and_hash_are_stable_for_mapping_order_and_nfc() -> None:
    first = {
        "z": [1, 2.5],
        "label": "e\u0301",
        "nested": {"z": 2, "a": 1},
    }
    second = {
        "nested": {"a": 1, "z": 2},
        "label": "é",
        "z": [1, 2.5],
    }

    expected = '{"label":"é","nested":{"a":1,"z":2},"z":[1,2.5]}'
    assert canonical_json(first) == expected
    assert canonical_json(first) == canonical_json(second)
    assert canonical_json_bytes(first) == expected.encode("utf-8")
    assert canonical_sha256(first) == canonical_sha256(second)


def test_json_loader_rejects_duplicate_object_keys() -> None:
    with pytest.raises(CanonicalizationError, match="duplicate object keys"):
        load_json('{"tool": "terminal", "tool": "other"}')


def test_canonical_json_rejects_duplicate_keys_after_nfc_normalization() -> None:
    with pytest.raises(CanonicalizationError, match="duplicate object keys"):
        canonical_json({"e\u0301": 1, "é": 2})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(CanonicalizationError, match="non-finite number"):
        canonical_json(value)


@pytest.mark.parametrize("payload", ['{"value": NaN}', '{"value": Infinity}'])
def test_json_loader_rejects_non_finite_numbers(payload: str) -> None:
    with pytest.raises(CanonicalizationError, match="non-JSON number"):
        load_json(payload)


@pytest.mark.parametrize("value", ["unsafe\x00value", object()])
def test_canonical_json_rejects_controls_and_unsupported_values(value: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json(value)


def test_hash_field_omission_applies_only_to_the_root_object() -> None:
    value = {
        "sha256": "root-digest",
        "nested": {"sha256": "nested-digest", "value": "kept"},
        "items": [{"sha256": "item-digest"}],
    }
    without_root_hash = '{"items":[{"sha256":"item-digest"}],"nested":{"sha256":"nested-digest","value":"kept"}}'

    assert canonical_json(value, omit_keys=("sha256",)) == without_root_hash
    assert canonical_sha256(value, omit_keys=("sha256",)) == hashlib.sha256(
        without_root_hash.encode("utf-8")
    ).hexdigest()
    with pytest.raises(CanonicalizationError, match="root object"):
        canonical_json([value], omit_keys=("sha256",))


def test_tool_call_sequence_hash_normalizes_record_key_order_and_ordinals() -> None:
    records = [_tool_call(1), _tool_call(2, name="memory", outcome="blocked")]
    reordered = [
        {key: record[key] for key in reversed(tuple(record))}
        for record in records
    ]

    assert hash_tool_call_sequence(records) == hash_tool_call_sequence(reordered)
    assert hash_tool_call_sequence(records) != hash_tool_call_sequence(
        [_tool_call(1), _tool_call(2, name="memory", outcome="executed")]
    )


def test_tool_call_sequence_rejects_raw_or_malformed_fields() -> None:
    with pytest.raises(CanonicalizationError, match="unexpected fields"):
        hash_tool_call_sequence([_tool_call(1) | {"arguments": {"synthetic": True}}])
    with pytest.raises(CanonicalizationError, match="invalid ordinal"):
        hash_tool_call_sequence([_tool_call(2)])


def test_trace_registry_is_closed_with_exact_actor_and_phase_mappings() -> None:
    assert TRACE_REGISTRY == _EXPECTED_TRACE_REGISTRY
    assert {actor for actor, _ in TRACE_REGISTRY.values()} == {
        "host",
        "plugin",
        "runner",
        "sdk",
    }
    assert {phase for _, phase in TRACE_REGISTRY.values()} == {
        "before_output",
        "lifecycle",
        "preflight",
        "side_effects",
        "terminal",
    }


@pytest.mark.parametrize(
    "code", ["usage.included", "usage.blocked", "usage.unknown"]
)
def test_usage_registry_entries_are_lifecycle_and_not_terminal(code: str) -> None:
    assert TRACE_REGISTRY[code] == ("plugin", "lifecycle")
    assert TRACE_REGISTRY[code][1] != "terminal"


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("terminal.complete", "complete"),
        ("terminal.cancelled", "cancelled"),
        ("terminal.failed", "failed"),
    ],
)
def test_terminal_registry_entries_are_terminal(code: str, kind: str) -> None:
    assert TRACE_REGISTRY[code] == ("plugin", "terminal")
    assert code.removeprefix("terminal.") == kind


def test_sdk_event_mapping_is_closed_and_exact() -> None:
    assert SDK_EVENT_CODES == {
        "ClaudeSDKClient.query": "sdk.query",
        "PreCompact": "sdk.precompact",
        "SystemMessage.compact_boundary": "sdk.compact_boundary",
        "ResultMessage": "sdk.result",
        "ToolUseBlock": "sdk.tool_use",
        "ToolResultBlock": "sdk.tool_result",
        "ClaudeSDKClient.resume": "sdk.resume",
        "HostToolBridge.handler": "host.execute_tool",
    }


def test_parity_import_is_sdk_lazy() -> None:
    source_root = Path(__file__).parents[2] / "src"
    probe = (
        "import sys\n"
        "import hermes_claude_agent_sdk.parity\n"
        "assert 'claude_agent_sdk' not in sys.modules\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        env={"PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

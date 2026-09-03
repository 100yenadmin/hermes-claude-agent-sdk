from __future__ import annotations

import builtins
from collections import Counter
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ExecutionClassification, ResultPacket
from hermes_claude_agent_sdk.parity.runner import ExecutionContext, ExecutionOutcome
from hermes_claude_agent_sdk.parity.trace import normalized_path_events
from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract, required_trial_indexes
from hermes_claude_agent_sdk.parity.v4_deterministic import (
    DETERMINISTIC_PACKET_COUNT,
    DETERMINISTIC_ROW_COUNT,
    V4DeterministicViolation,
    deterministic_category,
    run_deterministic,
    select_deterministic_rows,
)


ROOT = Path(__file__).parents[2]


def test_selection_is_exactly_provider_free_and_packet_closed() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")

    rows = select_deterministic_rows(contract)

    assert len(rows) == DETERMINISTIC_ROW_COUNT == 54
    assert sum(
        len(row["mandatory_paths"]) * len(required_trial_indexes(row))
        for row in rows
    ) == DETERMINISTIC_PACKET_COUNT == 148
    assert not any(row["provider_live_required"] for row in rows)
    assert Counter(deterministic_category(row) for row in rows) == Counter(
        {
            "approval_followthrough": 1,
            "active_focused": 3,
            "v2_mapped": 27,
            "boundary_focused": 23,
        }
    )


def test_provider_row_is_rejected_before_lazy_executor_resolution(monkeypatch) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    provider_row = next(row for row in contract["source_rows"] if row["provider_live_required"])
    imported = []

    def unexpected_import(*args, **kwargs):
        imported.append(args[0] if args else "unknown")
        raise AssertionError("provider/live executor was imported")

    monkeypatch.setattr("importlib.import_module", unexpected_import)

    with pytest.raises(V4DeterministicViolation, match="provider-live"):
        deterministic_category(provider_row)

    assert imported == []


def test_runner_emits_real_v3_packets_for_all_required_deterministic_trials(monkeypatch) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    calls = []

    async def fixture_executor(context: ExecutionContext) -> ExecutionOutcome:
        calls.append((context.capability.capability_id, context.path, context.trial_index))
        classification = (
            ExecutionClassification.EXPECTED_NEGATIVE
            if context.path == "denial"
            else ExecutionClassification.COMPLETE
        )
        evidence_hash = sha256_value(calls[-1])
        return ExecutionOutcome(
            classification=classification,
            billing_classification="none",
            normalized_events=normalized_path_events(
                context.capability.expected_trace,
                path=context.path,
                evidence_hash=evidence_hash,
            ),
            primary_proof_hash=evidence_hash,
            secondary_proof_hash=sha256_value({"candidate": context.plugin_sha}),
        )

    monkeypatch.setattr(
        "hermes_claude_agent_sdk.parity.v4_deterministic._resolve_executor",
        lambda row: fixture_executor,
    )

    packets = run_deterministic(
        contract,
        plugin_sha="a" * 40,
        host_sha="b" * 40,
        profile_id="fable-v3-isolated",
        profile_hash="c" * 64,
        sdk_version="0.2.151",
        inventory_hash="d" * 64,
    )

    assert len(packets) == DETERMINISTIC_PACKET_COUNT
    assert len(calls) == DETERMINISTIC_PACKET_COUNT
    assert all(isinstance(packet, ResultPacket) for packet in packets)
    assert {packet.schema_version for packet in packets} == {1}
    assert {packet.runner_version for packet in packets} == {"4.0.0"}
    assert all(not row["provider_live_required"] for row in select_deterministic_rows(contract))


def test_real_adapter_never_imports_full_registry(monkeypatch) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    imported = []
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "hermes_claude_agent_sdk.parity.executors":
            imported.append(name)
            raise AssertionError("provider/live registry was imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    packets = run_deterministic(
        contract,
        plugin_sha="a" * 40,
        host_sha="b" * 40,
        profile_id="fable-v3-isolated",
        profile_hash="c" * 64,
        sdk_version="0.2.151",
        inventory_hash="d" * 64,
    )

    assert len(packets) == DETERMINISTIC_PACKET_COUNT
    assert imported == []
    assert all(packet.billing_classification == "none" for packet in packets)

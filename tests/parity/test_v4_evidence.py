# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ExecutionClassification, ResultPacket, candidate_hash
from hermes_claude_agent_sdk.parity.trace import normalized_path_events
from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract
from hermes_claude_agent_sdk.parity.v4_evidence import V3_CATALOG_HASH, V3_CONTRACT_HASH, V4EvidenceViolation, bind_v4_evidence

ROOT = Path(__file__).parents[2]
H = "f" * 64
PREFLIGHTS = ("zero_native_absence", "exact_prompt_settings_tools_mcp", "no_native_events_projector", "delegate_owner", "background_owner", "canonical_transcript_content", "streaming_owner", "redaction_fail_closed")


def _trial(contract, classification=ExecutionClassification.COMPLETE):
    row = contract["source_rows"][0]
    passing = classification is ExecutionClassification.COMPLETE
    events = normalized_path_events(("start", "terminal"), path="positive", evidence_hash=H) if passing else () if classification is ExecutionClassification.PENDING else ({"sequence": 1, "kind": "terminal", "terminal_outcome": "failed"},)
    return ResultPacket.build(capability_id=row["predecessor_capability_id"], source_pack=row["source_pack"], lane="rc", path="positive", execution_id=row["predecessor_execution_id"], classification=classification, contract_hash=V3_CONTRACT_HASH, catalog_hash=V3_CATALOG_HASH, plugin_sha="a" * 40, host_sha="b" * 40, sdk_version="0.2.151", profile_id="isolated", profile_hash="c" * 64, runner_version="4.0.0", inventory_hash="3" * 64, billing_classification="none", trial_index=2, normalized_events=events, primary_proof_hash="4" * 64 if passing else None, secondary_proof_hash="5" * 64 if passing else None, reason_code=None if passing else "failed_trial")


def _receipt(trial):
    candidate = {"plugin_sha": trial.plugin_sha, "host_sha": trial.host_sha, "wheel_sha256": "6" * 64, "profile_sha256": trial.profile_hash, "sdk_distribution": "claude-agent-sdk", "sdk_version": "0.2.151", "cli_version": "2.1.258", "model": "claude-fable-5-1", "runner_id": "hermes-parity-v4", "runner_version": "4.0.0"}
    return {"schema_version": 1, "candidate": candidate, "candidate_hash": sha256_value(candidate), "trial_candidate_hash": trial.candidate_hash, "trial_index": trial.trial_index, "preflight_results": {name: {"status": "PASS", "evidence_sha256": "7" * 64} for name in PREFLIGHTS}, "proof_hashes": {"primary": trial.primary_proof_hash, "secondary": trial.secondary_proof_hash, "transcript": trial.trace_hash, "stream": "8" * 64}}


def test_binds_without_rewriting_observations() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial = _trial(contract)
    packet = bind_v4_evidence(contract, trial, _receipt(trial))
    assert (packet["classification"], packet["path"], packet["turn_count"]) == (trial.classification.value, trial.path, trial.turn_count)
    assert packet["events"] == [dict(event) for event in trial.normalized_events]
    assert packet["proof_hashes"]["stream"] == "8" * 64


@pytest.mark.parametrize("bad", [None, "not-a-trial"])
def test_rejects_absent_or_malformed_trial(bad) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    with pytest.raises(V4EvidenceViolation):
        bind_v4_evidence(contract, bad, {})


@pytest.mark.parametrize("classification", [ExecutionClassification.PENDING, ExecutionClassification.VERIFIED_FAILURE])
def test_rejects_incomplete_or_failed_trial(classification) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial = _trial(contract, classification)
    with pytest.raises(V4EvidenceViolation):
        bind_v4_evidence(contract, trial, _receipt(trial))


def test_rejects_contract_hash_mismatch() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial = _trial(contract).to_dict()
    trial["contract_hash"] = "1" * 64
    trial.pop("packet_hash")
    trial["packet_hash"] = sha256_value(trial)
    with pytest.raises(V4EvidenceViolation, match="contract hash"):
        bind_v4_evidence(contract, trial, _receipt(_trial(contract)))


def test_rejects_catalog_hash_mismatch() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial = _trial(contract).to_dict()
    trial["catalog_hash"] = "1" * 64
    trial["candidate_hash"] = candidate_hash(catalog_hash=trial["catalog_hash"], plugin_sha=trial["plugin_sha"], host_sha=trial["host_sha"], sdk_version=trial["sdk_version"], profile_hash=trial["profile_hash"], runner_version=trial["runner_version"], inventory_hash=trial["inventory_hash"])
    trial.pop("packet_hash")
    trial["packet_hash"] = sha256_value(trial)
    with pytest.raises(V4EvidenceViolation, match="catalog hash"):
        bind_v4_evidence(contract, trial, _receipt(_trial(contract)))


def test_rejects_candidate_mismatch_and_zero_preflight_digest() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial, receipt = _trial(contract), None
    receipt = _receipt(trial)
    receipt["candidate"]["host_sha"] = "d" * 40
    receipt["candidate_hash"] = sha256_value(receipt["candidate"])
    with pytest.raises(V4EvidenceViolation):
        bind_v4_evidence(contract, trial, receipt)
    receipt = _receipt(trial)
    receipt["preflight_results"]["delegate_owner"]["evidence_sha256"] = "0" * 64
    with pytest.raises(V4EvidenceViolation):
        bind_v4_evidence(contract, trial, receipt)


def test_rejects_nonterminal_trial_even_with_recomputed_packet_hash() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial = _trial(contract).to_dict()
    trial["normalized_events"] = trial["normalized_events"][:-1]
    trial["trace_hash"] = sha256_value(trial["normalized_events"])
    trial.pop("packet_hash")
    trial["packet_hash"] = sha256_value(trial)
    with pytest.raises(V4EvidenceViolation):
        bind_v4_evidence(contract, trial, _receipt(_trial(contract)))

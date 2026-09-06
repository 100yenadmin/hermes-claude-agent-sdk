from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ExecutionClassification, ResultPacket
from hermes_claude_agent_sdk.parity.trace import normalized_path_events
from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract
from hermes_claude_agent_sdk.parity.v4_evidence import bind_v4_evidence
from hermes_claude_agent_sdk.parity.v4_receipts import OWNERSHIP_PREFLIGHTS, OwnershipReceiptViolation, build_ownership_receipt

ROOT = Path(__file__).parents[2]
V3_CONTRACT = "aaddc44c53b5648202e34c5682a5c0ee599fa52b896c0530d0945cac95eb3244"
V3_CATALOG = "768c2d8f99077f8557a192d1053fc80401e83dee80d77475d12119df75b63abb"


def _trial(contract):
    row = contract["source_rows"][0]
    events = normalized_path_events(("start", "terminal"), path="positive", evidence_hash="f" * 64)
    return ResultPacket.build(capability_id=row["predecessor_capability_id"], source_pack=row["source_pack"], lane="rc", path="positive", execution_id=row["predecessor_execution_id"], classification=ExecutionClassification.COMPLETE, contract_hash=V3_CONTRACT, catalog_hash=V3_CATALOG, plugin_sha="a" * 40, host_sha="b" * 40, sdk_version="0.2.151", profile_id="isolated", profile_hash="c" * 64, runner_version="4.0.0", inventory_hash="d" * 64, billing_classification="subscription_included", trial_index=2, normalized_events=events, primary_proof_hash="4" * 64, secondary_proof_hash="5" * 64)


def _candidate(trial):
    return {"plugin_sha": trial.plugin_sha, "host_sha": trial.host_sha, "wheel_sha256": "6" * 64, "profile_sha256": trial.profile_hash, "sdk_distribution": "claude-agent-sdk", "sdk_version": "0.2.151", "cli_version": "2.1.258", "model": "claude-fable-5-1", "runner_id": "hermes-parity-v4", "runner_version": "4.0.0"}


def _projection(name, candidate_hash):
    return {"schema_version": 1, "name": name, "candidate_hash": candidate_hash, "status": "PASS", "source": {"executable": "tests/parity/preflight.py", "source_ref": f"tests/parity/{name}.py", "test_id": f"preflight:{name}"}, "observation": {"exit_status": 0, "checked": True, "observation_count": 1, "observation_hash": "1" * 64}}


def _stream(candidate_hash, trial):
    return {"schema_version": 1, "name": "stream", "candidate_hash": candidate_hash, "trial_candidate_hash": trial.candidate_hash, "trial_index": trial.trial_index, "status": "PASS", "source": {"executable": "tests/parity/stream.py", "source_ref": "tests/parity/stream.py", "test_id": "stream:trial"}, "observation": {"exit_status": 0, "chunk_count": 2, "event_count": len(trial.normalized_events), "content_hash": "2" * 64}}


def _inputs():
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    trial = _trial(contract)
    candidate = _candidate(trial)
    digest = sha256_value(candidate)
    projections = {name: _projection(name, digest) for name in OWNERSHIP_PREFLIGHTS}
    return contract, trial, candidate, digest, projections


def test_builds_receipt_that_bind_v4_evidence_accepts() -> None:
    contract, trial, candidate, digest, projections = _inputs()
    receipt = build_ownership_receipt(trial, candidate, projections, _stream(digest, trial))
    packet = bind_v4_evidence(contract, trial, receipt)
    assert receipt["candidate_hash"] == digest
    assert receipt["trial_candidate_hash"] == trial.candidate_hash
    assert receipt["trial_index"] == trial.trial_index
    assert receipt["proof_hashes"] == {"primary": trial.primary_proof_hash, "secondary": trial.secondary_proof_hash, "transcript": trial.trace_hash, "stream": sha256_value(_stream(digest, trial))}
    assert packet["candidate_hash"] == digest


@pytest.mark.parametrize("mutation", ["missing", "extra", "failed", "candidate"])
def test_rejects_missing_extra_failed_or_cross_candidate_preflight(mutation: str) -> None:
    _, trial, candidate, digest, projections = _inputs()
    if mutation == "missing":
        projections.pop("background_owner")
    elif mutation == "extra":
        projections["unexpected"] = _projection("unexpected", digest)
    elif mutation == "failed":
        projections["background_owner"]["status"] = "FAIL"
    else:
        projections["background_owner"]["candidate_hash"] = "0" * 64
    with pytest.raises(OwnershipReceiptViolation):
        build_ownership_receipt(trial, candidate, projections, _stream(digest, trial))


def test_rejects_synthetic_digest_and_cross_trial_or_raw_stream() -> None:
    _, trial, candidate, digest, projections = _inputs()
    projections["background_owner"]["evidence_sha256"] = "7" * 64
    with pytest.raises(OwnershipReceiptViolation):
        build_ownership_receipt(trial, candidate, projections, _stream(digest, trial))
    stream = _stream(digest, trial)
    stream["trial_index"] += 1
    with pytest.raises(OwnershipReceiptViolation):
        build_ownership_receipt(trial, candidate, projections, stream)
    projections = {name: _projection(name, digest) for name in OWNERSHIP_PREFLIGHTS}
    stream = _stream(digest, trial)
    stream["observation"] = {"raw_prompt": "must never be accepted"}
    with pytest.raises(OwnershipReceiptViolation):
        build_ownership_receipt(trial, candidate, projections, stream)


def test_persistence_refuses_pre_existing_output(tmp_path: Path) -> None:
    _, trial, candidate, digest, projections = _inputs()
    output = tmp_path / "receipt.json"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(OwnershipReceiptViolation):
        build_ownership_receipt(trial, candidate, projections, _stream(digest, trial), output=output)

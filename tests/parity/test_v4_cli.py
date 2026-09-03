from __future__ import annotations

import json
from pathlib import Path

from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ResultPacket
from hermes_claude_agent_sdk.parity.trace import normalized_path_events
from hermes_claude_agent_sdk.parity.v4_cli import main
from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract
from hermes_claude_agent_sdk.parity.v4_evidence import V3_CATALOG_HASH, V3_CONTRACT_HASH

ROOT = Path(__file__).parents[2]
PREFLIGHTS = ("zero_native_absence", "exact_prompt_settings_tools_mcp", "no_native_events_projector", "delegate_owner", "background_owner", "canonical_transcript_content", "streaming_owner", "redaction_fail_closed")


def _trial(contract, row_index=0, plugin_sha="a" * 40):
    row = contract["source_rows"][row_index]
    return ResultPacket.build(capability_id=row["predecessor_capability_id"], source_pack=row["source_pack"], lane="rc", path="positive", execution_id=row["predecessor_execution_id"], classification="COMPLETE", contract_hash=V3_CONTRACT_HASH, catalog_hash=V3_CATALOG_HASH, plugin_sha=plugin_sha, host_sha="b" * 40, sdk_version="0.2.151", profile_id="isolated", profile_hash="c" * 64, runner_version="4.0.0", inventory_hash="3" * 64, billing_classification="subscription_included", trial_index=1, normalized_events=normalized_path_events(row["expected_trace"], path="positive", evidence_hash="f" * 64), primary_proof_hash="4" * 64, secondary_proof_hash="5" * 64)


def _receipt(trial):
    candidate = {"plugin_sha": trial.plugin_sha, "host_sha": trial.host_sha, "wheel_sha256": "6" * 64, "profile_sha256": trial.profile_hash, "sdk_distribution": "claude-agent-sdk", "sdk_version": "0.2.151", "cli_version": "2.1.258", "model": "claude-fable-5-1", "runner_id": "hermes-parity-v4", "runner_version": "4.0.0"}
    return {"schema_version": 1, "candidate": candidate, "candidate_hash": sha256_value(candidate), "trial_candidate_hash": trial.candidate_hash, "trial_index": trial.trial_index, "preflight_results": {name: {"status": "PASS", "evidence_sha256": "7" * 64} for name in PREFLIGHTS}, "proof_hashes": {"primary": trial.primary_proof_hash, "secondary": trial.secondary_proof_hash, "transcript": trial.trace_hash, "stream": "8" * 64}}


def _inputs(tmp_path, *, plugin_sha="a" * 40):
    contract_path = ROOT / "qa" / "parity-contract-v4.yaml"
    contract = load_v4_contract(contract_path)
    packets, receipts = tmp_path / "v3", tmp_path / "receipts"
    packets.mkdir(); receipts.mkdir()
    trial = _trial(contract, plugin_sha=plugin_sha)
    (packets / "trial.json").write_text(json.dumps(trial.to_dict()), encoding="utf-8")
    (receipts / "trial.json").write_text(json.dumps(_receipt(trial)), encoding="utf-8")
    return contract_path, packets, receipts


def _run(contract, packets, receipts, output):
    return main(["bind-grade", "--contract", str(contract), "--v3-packets", str(packets), "--ownership-receipts", str(receipts), "--output", str(output)])


def test_bind_grade_cli_persists_bound_packet_and_report(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    output = tmp_path / "out"
    assert _run(contract, packets, receipts, output) == 75
    assert (output / "trial.json").is_file()
    report = json.loads((output / "grade-rc.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == 4
    assert (report["observed_packets"], report["status"]) == (1, "PARTIAL")


def test_bind_grade_cli_accepts_receipt_manifest(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    manifest = tmp_path / "receipts.json"
    manifest.write_text(json.dumps({"trial.json": json.loads((receipts / "trial.json").read_text())}), encoding="utf-8")
    assert _run(contract, packets, manifest, tmp_path / "out") == 75


def test_bind_grade_cli_rejects_extra_receipt_without_replacing_output(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    (receipts / "extra.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir(); sentinel = output / "sentinel.json"; sentinel.write_text("keep", encoding="utf-8")
    assert _run(contract, packets, receipts, output) == 2
    assert sentinel.read_text(encoding="utf-8") == "keep" and not (output / "trial.json").exists()


def test_bind_grade_cli_rejects_mixed_candidate_before_output(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    loaded = load_v4_contract(contract)
    second = _trial(loaded, row_index=1, plugin_sha="d" * 40)
    (packets / "second.json").write_text(json.dumps(second.to_dict()), encoding="utf-8")
    (receipts / "second.json").write_text(json.dumps(_receipt(second)), encoding="utf-8")
    assert _run(contract, packets, receipts, tmp_path / "out") == 2


def test_bind_grade_cli_rejects_conflicting_output_before_partial_persistence(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    (output / "trial.json").write_text("conflict", encoding="utf-8")
    assert _run(contract, packets, receipts, output) == 2
    assert not (output / "grade-rc.json").exists()


def test_bind_grade_cli_rejects_symlinked_input_directory_before_resolution(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    packet_link = tmp_path / "v3-link"
    packet_link.symlink_to(packets, target_is_directory=True)
    assert _run(contract, packet_link, receipts, tmp_path / "out") == 2
    assert not (tmp_path / "out").exists()


def test_bind_grade_cli_rejects_symlinked_output_before_resolution(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    target = tmp_path / "real-output"
    target.mkdir()
    output_link = tmp_path / "output-link"
    output_link.symlink_to(target, target_is_directory=True)
    assert _run(contract, packets, receipts, output_link) == 2
    assert list(target.iterdir()) == []


def test_bind_grade_cli_rejects_symlinked_lexical_ancestor_before_resolution(tmp_path: Path) -> None:
    contract, packets, receipts = _inputs(tmp_path)
    ancestor = tmp_path / "linked"
    ancestor.symlink_to(tmp_path, target_is_directory=True)
    assert _run(contract, ancestor / packets.name, receipts, tmp_path / "input-out") == 2
    assert not (tmp_path / "input-out").exists()
    assert _run(contract, packets, receipts, ancestor / "missing-parent" / "out") == 2
    assert not (tmp_path / "missing-parent").exists()

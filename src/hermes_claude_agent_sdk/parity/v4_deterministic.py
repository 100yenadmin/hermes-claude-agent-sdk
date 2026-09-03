"""Provider-free execution of the closed v4 predecessor slice.

Admission is complete before any executor import. The adapter emits ordinary
v3 ``ResultPacket`` values for later external v4 evidence binding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .catalog import Capability, Catalog, load_catalog
from .hashing import sha256_value
from .results import ExecutionClassification, ResultPacket
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome
from .trace import normalized_path_events
from .v4_contract import (
    V4_CLI_VERSION, V4_RUNNER_VERSION, V4_SDK_VERSION, V4ContractViolation,
    load_v4_contract, required_trial_indexes, validate_v4_contract,
)

DETERMINISTIC_ROW_COUNT = 54
DETERMINISTIC_PACKET_COUNT = 148
_PACK_COUNTS = {"v2_non_soak": 27, "openclaw_active": 4, "agent_sdk_boundary": 23}
_ACTIVE_FOCUSED_IDS = frozenset({"model-switch-tool-continuity", "compaction-retry-mutating-tool", "subagent-stale-child-links"})
_APPROVAL_ID = "approval-turn-tool-followthrough"
_CATEGORY_COUNTS = Counter({"approval_followthrough": 1, "active_focused": 3, "v2_mapped": 27, "boundary_focused": 23})


class V4DeterministicViolation(ValueError):
    """The closed provider-free v4 execution slice is not admissible."""


def _digest(value: Any, length: int, field: str) -> None:
    if not isinstance(value, str) or len(value) != length or any(char not in "0123456789abcdef" for char in value):
        raise V4DeterministicViolation(f"{field} is not a lowercase digest")


def deterministic_category(row: Mapping[str, Any]) -> str:
    """Return the one admitted category for a provider-free source row."""
    if not isinstance(row, Mapping) or row.get("provider_live_required") is not False:
        raise V4DeterministicViolation("provider-live row cannot enter deterministic execution")
    pack, source_id, execution_id = (row.get(key) for key in ("source_pack", "source_item_id", "predecessor_execution_id"))
    if not all(isinstance(value, str) and value for value in (pack, source_id, execution_id)):
        raise V4DeterministicViolation("deterministic row identity is malformed")
    if pack == "openclaw_active" and source_id == _APPROVAL_ID:
        if execution_id != "active-approval-turn-tool-followthrough":
            raise V4DeterministicViolation("approval executor identity does not match v4 predecessor")
        return "approval_followthrough"
    if pack == "openclaw_active" and source_id in _ACTIVE_FOCUSED_IDS:
        if execution_id != f"active-{source_id}":
            raise V4DeterministicViolation("active focused executor identity does not match predecessor")
        return "active_focused"
    if pack == "v2_non_soak" and execution_id.startswith("v2-"):
        return "v2_mapped"
    if pack == "agent_sdk_boundary" and execution_id.startswith("boundary-"):
        return "boundary_focused"
    raise V4DeterministicViolation("row has no admitted deterministic executor category")


def select_deterministic_rows(contract: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Validate v4 and select exactly its zero-provider rows and packets."""
    try:
        validate_v4_contract(contract)
    except V4ContractViolation as exc:
        raise V4DeterministicViolation("v4 contract is not admissible") from exc
    rows = contract.get("source_rows")
    if not isinstance(rows, list):
        raise V4DeterministicViolation("v4 source rows are malformed")
    selected = tuple(row for row in rows if isinstance(row, Mapping) and row.get("provider_live_required") is False)
    categories = Counter(deterministic_category(row) for row in selected)
    counts = Counter(row["source_pack"] for row in selected)
    packets = sum(len(row["mandatory_paths"]) * len(required_trial_indexes(row)) for row in selected)
    provider_keys = {(row["source_pack"], row["source_item_id"]) for row in rows if isinstance(row, Mapping) and row.get("provider_live_required") is True}
    selected_keys = {(row["source_pack"], row["source_item_id"]) for row in selected}
    if (len(selected), dict(counts), packets, selected_keys & provider_keys, categories) != (DETERMINISTIC_ROW_COUNT, _PACK_COUNTS, DETERMINISTIC_PACKET_COUNT, set(), _CATEGORY_COUNTS):
        raise V4DeterministicViolation("v4 deterministic row or packet accounting drifted")
    return selected


def _bundle(classification: ExecutionClassification, reason: str, events: Sequence[Mapping[str, Any]] = ()) -> ExecutionBundle:
    return ExecutionBundle(outcomes={path: ExecutionOutcome(classification=classification, billing_classification="none", normalized_events=tuple(events), reason_code=reason) for path in ("positive", "denial", "recovery")}, turn_count=0)


def _approval_report_hash(report: Mapping[str, Any]) -> str:
    fields = ("status", "execution_path", "tool", "approval_requests", "approval_outcomes", "tool_outcomes", "host_execute_tool_calls", "runtime_tool_requests", "runtime_usage_events", "runtime_terminal_events", "provider_calls", "auth_calls", "synthetic_auth_probe_calls", "network_calls", "raw_payloads", "shared_state")
    return sha256_value({key: report.get(key) for key in fields})


async def _approval_executor(context: ExecutionContext) -> ExecutionBundle:
    """Adapt the existing offline approval fixture to the runner contract."""
    if context.profile_id != "fable-v3-isolated" or context.sdk_version != V4_SDK_VERSION:
        return _bundle(ExecutionClassification.ENVIRONMENT_BLOCKED, "deterministic_candidate_identity_mismatch")
    host_root = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not host_root:
        return _bundle(ExecutionClassification.ENVIRONMENT_BLOCKED, "approval_host_root_unconfigured")
    if os.environ.get("HERMES_PARITY_PLUGIN_SHA") != context.plugin_sha:
        return _bundle(ExecutionClassification.ENVIRONMENT_BLOCKED, "plugin_sha_unverified")
    if os.environ.get("HERMES_AGENT_HOST_SHA") != context.host_sha:
        return _bundle(ExecutionClassification.ENVIRONMENT_BLOCKED, "host_sha_unverified")
    try:
        from .approval_followthrough import run_approval_followthrough
        report = run_approval_followthrough(host_root=host_root)
    except Exception:  # noqa: BLE001 - fixture faults fail closed
        return _bundle(ExecutionClassification.VERIFIED_FAILURE, "approval_deterministic_executor_failed", (("sequence", 1), ("kind", "terminal"), ("status", "failed"), ("terminal_outcome", "failed")))
    if not isinstance(report, Mapping):
        return _bundle(ExecutionClassification.VERIFIED_FAILURE, "approval_deterministic_report_malformed", (("sequence", 1), ("kind", "terminal"), ("status", "failed"), ("terminal_outcome", "failed")))
    if any(report.get(field) != 0 for field in ("provider_calls", "auth_calls", "network_calls", "raw_payloads")) or report.get("shared_state") != "temporary_only":
        return _bundle(ExecutionClassification.VERIFIED_FAILURE, "approval_fixture_provider_boundary_failed", (("sequence", 1), ("kind", "terminal"), ("status", "failed"), ("terminal_outcome", "failed")))
    if report.get("status") != "passed":
        return _bundle(ExecutionClassification.VERIFIED_FAILURE, "approval_deterministic_fixture_failed", (("sequence", 1), ("kind", "terminal"), ("status", "failed"), ("terminal_outcome", "failed")))
    evidence_hash = _approval_report_hash(report)
    outcomes = {}
    for path in ("positive", "denial", "recovery"):
        outcomes[path] = ExecutionOutcome(
            classification=ExecutionClassification.EXPECTED_NEGATIVE if path == "denial" else ExecutionClassification.COMPLETE,
            billing_classification="none",
            normalized_events=normalized_path_events(context.capability.expected_trace, path=path, evidence_hash=evidence_hash),
            primary_proof_hash=sha256_value({"evidence": evidence_hash, "path": path}),
            secondary_proof_hash=sha256_value({"catalog_hash": context.catalog_hash, "profile_hash": context.profile_hash, "path": path}),
        )
    return ExecutionBundle(outcomes=outcomes, turn_count=0)


def _resolve_executor(row: Mapping[str, Any]) -> Callable[..., Any]:
    """Resolve one category only after ``select_deterministic_rows`` passes."""
    category = deterministic_category(row)
    if category == "approval_followthrough":
        return _approval_executor
    if category == "active_focused":
        from .active_suite import active_agentic_suite
        return active_agentic_suite
    if category == "v2_mapped":
        from .v2_suite import v2_mapped_suite
        return v2_mapped_suite
    if category == "boundary_focused":
        from .focused_suite import boundary_focused_suite
        return boundary_focused_suite
    raise V4DeterministicViolation("unknown deterministic executor category")


resolve_deterministic_executor = _resolve_executor


def _catalog_for_contract(contract: Mapping[str, Any]) -> Catalog:
    source = contract.get("_path")
    if not isinstance(source, str) or not source:
        raise V4DeterministicViolation("v4 contract path is required to bind predecessor catalog")
    try:
        return load_catalog(Path(source).expanduser().resolve().parent / "parity-contract-v3.yaml")
    except Exception as exc:  # noqa: BLE001 - immutable predecessor faults fail closed
        raise V4DeterministicViolation("immutable v3 predecessor catalog is unavailable") from exc


def _context(capability: Capability, *, path: str, trial_index: int, catalog: Catalog, plugin_sha: str, host_sha: str, profile_id: str, profile_hash: str, sdk_version: str, inventory_hash: str, inventory_tools: Sequence[Mapping[str, str]], profile_isolation_kind: str, profile_persistent: bool) -> ExecutionContext:
    return ExecutionContext(capability=capability, path=path, trial_index=trial_index, profile_id=profile_id, profile_hash=profile_hash, plugin_sha=plugin_sha, host_sha=host_sha, sdk_version=sdk_version, runner_version=V4_RUNNER_VERSION, inventory_hash=inventory_hash, contract_hash=catalog.contract_hash, catalog_hash=catalog.catalog_hash, remaining_turn_budget=180, repo_root=str(catalog.path.parent.parent), inventory_tools=tuple(inventory_tools), profile_isolation_kind=profile_isolation_kind, profile_persistent=profile_persistent)


async def _run_deterministic_async(contract: Mapping[str, Any], *, plugin_sha: str, host_sha: str, profile_id: str, profile_hash: str, sdk_version: str, inventory_hash: str, runner_version: str = V4_RUNNER_VERSION, inventory_tools: Sequence[Mapping[str, str]] = (), profile_isolation_kind: str = "", profile_persistent: bool = False) -> tuple[ResultPacket, ...]:
    rows = select_deterministic_rows(contract)
    if sdk_version != V4_SDK_VERSION or runner_version != V4_RUNNER_VERSION or profile_id != "fable-v3-isolated":
        raise V4DeterministicViolation("v4 deterministic candidate identity is unsupported")
    _digest(plugin_sha, 40, "plugin_sha"); _digest(host_sha, 40, "host_sha"); _digest(profile_hash, 64, "profile_sha"); _digest(inventory_hash, 64, "inventory_sha")
    catalog = _catalog_for_contract(contract)
    capabilities = {}
    for row in rows:
        key = (row["source_pack"], row["source_item_id"])
        capability = catalog.by_id.get(row["predecessor_capability_id"])
        if capability is None or (capability.source_pack, capability.source_item_id) != key or capability.execution_id != row["predecessor_execution_id"]:
            raise V4DeterministicViolation("v4 row does not bind a unique v3 capability")
        capabilities[key] = capability
    from .runner import _call_executor, _validate_executor_result
    packets: list[ResultPacket] = []
    bundles: dict[tuple[str, int], ExecutionBundle] = {}
    for row in rows:
        capability = capabilities[(row["source_pack"], row["source_item_id"])]
        executor = _resolve_executor(row)
        for trial_index in required_trial_indexes(row):
            key = (capability.execution_id, trial_index)
            result: ExecutionBundle | ExecutionOutcome | None = bundles.get(key)
            for path in row["mandatory_paths"]:
                if result is None:
                    result = await _call_executor(executor, _context(capability, path=path, trial_index=trial_index, catalog=catalog, plugin_sha=plugin_sha, host_sha=host_sha, profile_id=profile_id, profile_hash=profile_hash, sdk_version=sdk_version, inventory_hash=inventory_hash, inventory_tools=inventory_tools, profile_isolation_kind=profile_isolation_kind, profile_persistent=profile_persistent))
                    _validate_executor_result(result, 180)
                    if isinstance(result, ExecutionBundle):
                        bundles[key] = result
                if isinstance(result, ExecutionBundle):
                    if set(result.outcomes) != {"positive", "denial", "recovery"}:
                        raise V4DeterministicViolation("deterministic bundle paths are not closed")
                    outcome = result.outcomes[path]
                else:
                    outcome = result
                packets.append(ResultPacket.build(capability_id=capability.capability_id, source_pack=capability.source_pack, lane=capability.lane, path=path, execution_id=capability.execution_id, classification=outcome.classification, contract_hash=catalog.contract_hash, catalog_hash=catalog.catalog_hash, plugin_sha=plugin_sha, host_sha=host_sha, sdk_version=sdk_version, profile_id=profile_id, profile_hash=profile_hash, runner_version=V4_RUNNER_VERSION, inventory_hash=inventory_hash, billing_classification=outcome.billing_classification, turn_count=outcome.turn_count, trial_index=trial_index, normalized_events=outcome.normalized_events, primary_proof_hash=outcome.primary_proof_hash, secondary_proof_hash=outcome.secondary_proof_hash, silent_fallback=outcome.silent_fallback, invariant_violations=outcome.invariant_violations, reason_code=outcome.reason_code))
                if not isinstance(result, ExecutionBundle):
                    result = None
    if len(packets) != DETERMINISTIC_PACKET_COUNT:
        raise V4DeterministicViolation("deterministic packet emission count drifted")
    return tuple(packets)


def run_deterministic(contract: Mapping[str, Any], **kwargs: Any) -> tuple[ResultPacket, ...]:
    """Run only admitted rows and return real v3 predecessor packets."""
    return asyncio.run(_run_deterministic_async(contract, **kwargs))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-claude-agent-sdk-parity-v4-deterministic")
    for name in ("contract", "plugin-sha", "host-sha", "profile-sha", "inventory-sha"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--profile-id", default="fable-v3-isolated"); parser.add_argument("--sdk-version", default=V4_SDK_VERSION); parser.add_argument("--output", type=Path)
    return parser


def _write_packets(output: Path, packets: Sequence[ResultPacket]) -> None:
    output = output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise V4DeterministicViolation("packet output must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{packet.capability_id}__{packet.path}__trial-{packet.trial_index:03d}.json" for packet in packets]
    if any(target.exists() for target in targets):
        raise V4DeterministicViolation("refusing to replace pre-existing predecessor packet")
    for target, packet in zip(targets, packets, strict=True):
        target.write_text(json.dumps(packet.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        packets = run_deterministic(load_v4_contract(args.contract), plugin_sha=args.plugin_sha, host_sha=args.host_sha, profile_id=args.profile_id, profile_hash=args.profile_sha, sdk_version=args.sdk_version, inventory_hash=args.inventory_sha)
        if args.output is not None:
            _write_packets(args.output, packets)
        print(json.dumps({"runner_version": V4_RUNNER_VERSION, "sdk_version": args.sdk_version, "cli_version": V4_CLI_VERSION, "selected_rows": DETERMINISTIC_ROW_COUNT, "required_packets": DETERMINISTIC_PACKET_COUNT, "observed_packets": len(packets), "provider_calls": 0, "proof_boundary": "Provider-free predecessor packets only; ownership receipts, v4 binding, grading, release, runtime, and customer readiness remain external."}, sort_keys=True))
        return 0
    except (V4DeterministicViolation, V4ContractViolation, OSError, ValueError) as exc:
        print(f"deterministic contract violation: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DETERMINISTIC_PACKET_COUNT", "DETERMINISTIC_ROW_COUNT", "V4DeterministicViolation", "deterministic_category", "main", "resolve_deterministic_executor", "run_deterministic", "select_deterministic_rows"]

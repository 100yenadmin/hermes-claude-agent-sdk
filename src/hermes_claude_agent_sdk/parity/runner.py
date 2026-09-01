"""Resume-safe execution over explicitly registered parity executors."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from .catalog import Capability, Catalog
from .grader import GradeReport, grade_packets
from .hashing import json_compatible, sha256_value
from .results import (
    ExecutionClassification,
    ResultPacket,
    ResultViolation,
    candidate_hash,
    read_result_packet,
)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    capability: Capability
    path: str
    trial_index: int
    profile_id: str
    profile_hash: str
    plugin_sha: str
    host_sha: str
    sdk_version: str
    runner_version: str
    inventory_hash: str
    contract_hash: str
    catalog_hash: str
    remaining_turn_budget: int
    repo_root: str = ""
    inventory_tools: tuple[Mapping[str, str], ...] = ()
    profile_isolation_kind: str = ""
    profile_persistent: bool = False
    output_dir: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    classification: ExecutionClassification
    billing_classification: str
    normalized_events: tuple[Mapping[str, Any], ...] = ()
    primary_proof_hash: str | None = None
    secondary_proof_hash: str | None = None
    silent_fallback: bool = False
    invariant_violations: tuple[str, ...] = ()
    reason_code: str | None = None
    turn_count: int = 0


@dataclass(frozen=True, slots=True)
class ExecutionBundle:
    """One scenario execution that proves all three catalog paths."""

    outcomes: Mapping[str, ExecutionOutcome]
    turn_count: int


ExecutorResult = ExecutionOutcome | ExecutionBundle
Executor = Callable[[ExecutionContext], ExecutorResult | Awaitable[ExecutorResult]]


_TURN_BUDGETS = {"rc": 180, "runtime": 100}
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "lane",
        "contract_hash",
        "catalog_hash",
        "candidate_hash",
        "turn_budget",
        "executions",
        "packet_hashes",
        "manifest_hash",
    }
)


class ExecutorRegistry:
    """Exact execution-id registry; unknown ids never fall back."""

    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, execution_id: str, executor: Executor) -> None:
        if not execution_id or execution_id in self._executors:
            raise ResultViolation(f"executor registration is invalid or duplicate: {execution_id}")
        self._executors[execution_id] = executor

    def get(self, execution_id: str) -> Executor | None:
        return self._executors.get(execution_id)

    @property
    def execution_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._executors))


def load_entrypoint_executors() -> ExecutorRegistry:
    """Load opt-in executor mappings without inventing an implicit adapter."""

    registry = ExecutorRegistry()
    entry_points = metadata.entry_points()
    selected = entry_points.select(group="hermes_claude_agent_sdk.parity_executors")
    for entry_point in sorted(selected, key=lambda item: item.name):
        loaded = entry_point.load()
        if isinstance(loaded, Mapping):
            for execution_id, executor in loaded.items():
                if not isinstance(execution_id, str) or not callable(executor):
                    raise ResultViolation(f"parity executor entry point {entry_point.name} is malformed")
                registry.register(execution_id, executor)
        elif callable(loaded):
            registry.register(entry_point.name, loaded)
        else:
            raise ResultViolation(f"parity executor entry point {entry_point.name} is malformed")
    return registry


def _result_path(output: Path, capability: Capability, path: str, trial_index: int) -> Path:
    return output / f"{capability.capability_id}__{path}__trial-{trial_index:03d}.json"


async def _call_executor(executor: Executor, context: ExecutionContext) -> ExecutorResult:
    try:
        result = executor(context)
        if inspect.isawaitable(result):
            result = await result
    except asyncio.CancelledError:
        raise
    except Exception:
        return ExecutionOutcome(
            classification=ExecutionClassification.VERIFIED_FAILURE,
            billing_classification="none",
            normalized_events=(
                {
                    "sequence": 1,
                    "kind": "terminal",
                    "status": "executor_exception",
                    "terminal_outcome": "failed",
                },
            ),
            reason_code="executor_exception",
        )
    if not isinstance(result, (ExecutionOutcome, ExecutionBundle)):
        raise ResultViolation("parity executor returned an unsupported outcome")
    return result


def _outcome_fingerprint(outcome: ExecutionOutcome) -> dict[str, Any]:
    return {
        "classification": ExecutionClassification(outcome.classification).value,
        "billing_classification": outcome.billing_classification,
        "normalized_events": [dict(event) for event in outcome.normalized_events],
        "primary_proof_hash": outcome.primary_proof_hash,
        "secondary_proof_hash": outcome.secondary_proof_hash,
        "silent_fallback": outcome.silent_fallback,
        "invariant_violations": list(outcome.invariant_violations),
        "reason_code": outcome.reason_code,
        "turn_count": outcome.turn_count,
    }


def _validate_executor_result(result: ExecutorResult, remaining: int) -> None:
    def validate_outcome(outcome: ExecutionOutcome) -> None:
        try:
            ExecutionClassification(outcome.classification)
        except (TypeError, ValueError) as exc:
            raise ResultViolation("executor classification is unsupported") from exc
        if type(outcome.turn_count) is not int or outcome.turn_count < 0:
            raise ResultViolation("executor turn_count must be a non-negative integer")

    if isinstance(result, ExecutionOutcome):
        validate_outcome(result)
        if result.turn_count > remaining:
            raise ResultViolation("executor exceeded the remaining authorized turn budget")
        return
    if set(result.outcomes) != {"positive", "denial", "recovery"}:
        raise ResultViolation("combined executor must return positive, denial, and recovery outcomes")
    if any(not isinstance(outcome, ExecutionOutcome) for outcome in result.outcomes.values()):
        raise ResultViolation("combined executor outcomes are malformed")
    for outcome in result.outcomes.values():
        validate_outcome(outcome)
    if type(result.turn_count) is not int or result.turn_count < 0:
        raise ResultViolation("combined executor turn_count must be a non-negative integer")
    if result.turn_count != sum(outcome.turn_count for outcome in result.outcomes.values()):
        raise ResultViolation("combined executor turn_count must equal its path turn counts")
    if result.turn_count > remaining:
        raise ResultViolation("combined executor exceeded the remaining authorized turn budget")


def _validate_runtime_campaign(
    result: ExecutorResult,
    *,
    lane: str,
    execution_id: str,
) -> None:
    if lane != "runtime" or execution_id != "runtime-active-100-turn":
        return
    if not isinstance(result, ExecutionBundle):
        raise ResultViolation("runtime qualification requires one combined campaign")
    passing = (
        result.outcomes["positive"].classification
        is ExecutionClassification.COMPLETE
        and result.outcomes["denial"].classification
        is ExecutionClassification.EXPECTED_NEGATIVE
        and result.outcomes["recovery"].classification
        is ExecutionClassification.COMPLETE
    )
    if passing and result.turn_count != _TURN_BUDGETS["runtime"]:
        raise ResultViolation("passing runtime qualification must bind exactly 100 turns")


def _execution_receipt(
    *,
    exact_candidate: str,
    execution_id: str,
    path: str | None,
    trial_index: int,
    result: ExecutorResult,
) -> dict[str, Any]:
    if isinstance(result, ExecutionBundle):
        fingerprint = {
            name: _outcome_fingerprint(outcome)
            for name, outcome in sorted(result.outcomes.items())
        }
        turn_count = result.turn_count
        scope = "bundle"
    else:
        fingerprint = _outcome_fingerprint(result)
        turn_count = result.turn_count
        scope = "path"
    receipt_id = sha256_value(
        {
            "candidate_hash": exact_candidate,
            "execution_id": execution_id,
            "scope": scope,
            "path": path,
            "trial_index": trial_index,
            "turn_count": turn_count,
            "outcome": fingerprint,
        }
    )
    return {
        "receipt_id": receipt_id,
        "execution_id_hash": sha256_value(execution_id),
        "scope": scope,
        "path": path,
        "trial_index": trial_index,
        "turn_count": turn_count,
    }


def _manifest_payload(
    *,
    lane: str,
    catalog: Catalog,
    exact_candidate: str,
    executions: Sequence[Mapping[str, Any]],
    packet_hashes: Sequence[str],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "lane": lane,
        "contract_hash": catalog.contract_hash,
        "catalog_hash": catalog.catalog_hash,
        "candidate_hash": exact_candidate,
        "turn_budget": _TURN_BUDGETS[lane],
        "executions": [dict(item) for item in executions],
        "packet_hashes": sorted(packet_hashes),
    }
    value["manifest_hash"] = sha256_value(value)
    return value


def _load_manifest(
    path: Path,
    *,
    lane: str,
    catalog: Catalog,
    exact_candidate: str,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise ResultViolation("run manifest is missing or exceeds the bounded file size")
    try:
        loaded = json_compatible(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ResultViolation(f"run manifest cannot be read safely: {exc}") from exc
    if not isinstance(loaded, Mapping) or set(loaded) != _MANIFEST_FIELDS:
        raise ResultViolation("run manifest fields do not match schema")
    if (
        loaded["schema_version"] != 1
        or loaded["lane"] != lane
        or loaded["contract_hash"] != catalog.contract_hash
        or loaded["catalog_hash"] != catalog.catalog_hash
        or loaded["candidate_hash"] != exact_candidate
        or loaded["turn_budget"] != _TURN_BUDGETS[lane]
    ):
        raise ResultViolation("run manifest does not match the exact candidate and lane")
    without_hash = dict(loaded)
    manifest_hash = without_hash.pop("manifest_hash")
    if manifest_hash != sha256_value(without_hash):
        raise ResultViolation("run manifest hash does not match its content")
    executions = loaded["executions"]
    packet_hashes = loaded["packet_hashes"]
    if not isinstance(executions, list) or not isinstance(packet_hashes, list):
        raise ResultViolation("run manifest executions and packet_hashes must be lists")
    receipts: list[dict[str, Any]] = []
    receipt_ids: set[str] = set()
    for raw in executions:
        if not isinstance(raw, Mapping) or set(raw) != {
            "receipt_id",
            "execution_id_hash",
            "scope",
            "path",
            "trial_index",
            "turn_count",
        }:
            raise ResultViolation("run manifest execution receipt is malformed")
        if raw["receipt_id"] in receipt_ids:
            raise ResultViolation("run manifest contains a duplicate execution receipt")
        if raw["scope"] not in {"bundle", "path"}:
            raise ResultViolation("run manifest execution scope is unsupported")
        if not isinstance(raw["receipt_id"], str) or len(raw["receipt_id"]) != 64:
            raise ResultViolation("run manifest receipt_id is malformed")
        if not isinstance(raw["execution_id_hash"], str) or len(raw["execution_id_hash"]) != 64:
            raise ResultViolation("run manifest execution_id_hash is malformed")
        if type(raw["trial_index"]) is not int or raw["trial_index"] < 1:
            raise ResultViolation("run manifest trial_index is malformed")
        if raw["scope"] == "path" and raw["path"] not in {"positive", "denial", "recovery"}:
            raise ResultViolation("run manifest path receipt is malformed")
        if raw["scope"] == "bundle" and raw["path"] is not None:
            raise ResultViolation("run manifest bundle receipt cannot name one path")
        if type(raw["turn_count"]) is not int or raw["turn_count"] < 0:
            raise ResultViolation("run manifest turn_count is malformed")
        receipt_ids.add(raw["receipt_id"])
        receipts.append(dict(raw))
    if sum(item["turn_count"] for item in receipts) > _TURN_BUDGETS[lane]:
        raise ResultViolation("run manifest exceeds the authorized turn budget")
    if any(not isinstance(value, str) or len(value) != 64 for value in packet_hashes):
        raise ResultViolation("run manifest packet hashes are malformed")
    return receipts, set(packet_hashes)


def _write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_run_manifest(
    output: str | Path,
    *,
    lane: str,
    catalog: Catalog,
    exact_candidate: str,
) -> dict[str, int]:
    """Validate the resume ledger and exact result packet set for grading."""

    output_path = Path(output).expanduser().resolve()
    receipts, recorded_packet_hashes = _load_manifest(
        output_path / "run-manifest.json",
        lane=lane,
        catalog=catalog,
        exact_candidate=exact_candidate,
    )
    packets = [
        read_result_packet(path)
        for path in sorted(output_path.glob("*__*__trial-*.json"))
    ]
    if {packet.packet_hash for packet in packets} != recorded_packet_hashes:
        raise ResultViolation("run manifest packet set does not match the result directory")
    if any(packet.candidate_hash != exact_candidate or packet.lane != lane for packet in packets):
        raise ResultViolation("result directory mixes candidate identities or lanes")
    return {
        "turn_budget": _TURN_BUDGETS[lane],
        "turns_used": sum(item["turn_count"] for item in receipts),
        "execution_receipts": len(receipts),
        "result_packets": len(packets),
    }


async def run_catalog_async(
    catalog: Catalog,
    *,
    lane: str,
    profile_id: str,
    profile_hash: str,
    plugin_sha: str,
    host_sha: str,
    sdk_version: str,
    runner_version: str,
    inventory_hash: str,
    output: str | Path,
    resume: bool,
    registry: ExecutorRegistry,
    inventory_tools: Sequence[Mapping[str, str]] = (),
    capability_ids: Sequence[str] = (),
    max_trials_per_path: int = 6,
    profile_isolation_kind: str = "",
    profile_persistent: bool = False,
) -> tuple[tuple[ResultPacket, ...], GradeReport]:
    """Execute required paths and return packets plus the deterministic grade."""

    if max_trials_per_path < 3:
        raise ResultViolation("max_trials_per_path must allow strict three-pass recovery")
    output_path = Path(output).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    requested = set(capability_ids)
    available = {item.capability_id for item in catalog.for_lane(lane)}
    unknown = requested - available
    if unknown:
        raise ResultViolation(f"requested capability ids are not in lane {lane}: {sorted(unknown)}")
    capabilities = tuple(
        item for item in catalog.for_lane(lane) if not requested or item.capability_id in requested
    )
    exact_candidate = candidate_hash(
        catalog_hash=catalog.catalog_hash,
        plugin_sha=plugin_sha,
        host_sha=host_sha,
        sdk_version=sdk_version,
        profile_hash=profile_hash,
        runner_version=runner_version,
        inventory_hash=inventory_hash,
    )
    manifest_path = output_path / "run-manifest.json"
    existing_paths = sorted(output_path.glob("*__*__trial-*.json"))
    existing_packets: list[ResultPacket] = []
    if resume:
        if manifest_path.exists():
            execution_receipts, recorded_packet_hashes = _load_manifest(
                manifest_path,
                lane=lane,
                catalog=catalog,
                exact_candidate=exact_candidate,
            )
            existing_packets = [read_result_packet(path) for path in existing_paths]
            if {packet.packet_hash for packet in existing_packets} != recorded_packet_hashes:
                raise ResultViolation("run manifest packet set does not match the result directory")
        elif existing_paths:
            raise ResultViolation("resume requires the matching run manifest")
        else:
            execution_receipts, recorded_packet_hashes = [], set()
    else:
        if manifest_path.exists() or existing_paths:
            raise ResultViolation("fresh run refuses an existing manifest or result packet")
        execution_receipts, recorded_packet_hashes = [], set()
    del recorded_packet_hashes
    turn_budget = _TURN_BUDGETS[lane]
    used_turns = sum(item["turn_count"] for item in execution_receipts)
    packets: list[ResultPacket] = []
    outcome_cache: dict[tuple[str, str, int], ExecutionOutcome] = {}
    bundle_packet_cache: dict[tuple[str, int], dict[str, ResultPacket]] = {}
    checkpoint_packets = {
        path: packet for path, packet in zip(existing_paths, existing_packets, strict=True)
    }
    scenario_repeat_targets: dict[str, int] = {}
    global_environment_block_reasons = {
        "active_subscription_limit_reached",
        "active_synthetic_provider_notice",
        "native_subscription_limit_reached",
        "native_synthetic_provider_notice",
    }
    abort_run = False

    def build_packet(
        capability: Capability,
        path: str,
        trial_index: int,
        outcome: ExecutionOutcome,
    ) -> ResultPacket:
        return ResultPacket.build(
            capability_id=capability.capability_id,
            source_pack=capability.source_pack,
            lane=capability.lane,
            path=path,
            execution_id=capability.execution_id,
            classification=outcome.classification,
            contract_hash=catalog.contract_hash,
            catalog_hash=catalog.catalog_hash,
            plugin_sha=plugin_sha,
            host_sha=host_sha,
            sdk_version=sdk_version,
            profile_id=profile_id,
            profile_hash=profile_hash,
            runner_version=runner_version,
            inventory_hash=inventory_hash,
            billing_classification=outcome.billing_classification,
            turn_count=outcome.turn_count,
            trial_index=trial_index,
            normalized_events=outcome.normalized_events,
            primary_proof_hash=outcome.primary_proof_hash,
            secondary_proof_hash=outcome.secondary_proof_hash,
            silent_fallback=outcome.silent_fallback,
            invariant_violations=outcome.invariant_violations,
            reason_code=outcome.reason_code,
        )

    def checkpoint_manifest() -> None:
        manifest = _manifest_payload(
            lane=lane,
            catalog=catalog,
            exact_candidate=exact_candidate,
            executions=execution_receipts,
            packet_hashes=[
                packet.packet_hash for packet in checkpoint_packets.values()
            ],
        )
        _write_manifest(manifest_path, manifest)

    for capability in capabilities:
        if abort_run:
            break
        for path in ("positive", "denial", "recovery"):
            if abort_run:
                break
            if not capability.path(path)["required"]:
                continue
            triggers = set(capability.repeat_policy["triggers"])
            target = int(capability.repeat_policy["consecutive_passes"])
            if triggers & {"consequential", "unstable"}:
                target = max(target, 3)
            target = max(target, scenario_repeat_targets.get(capability.execution_id, 1))
            trial_index = 1
            while trial_index <= target and trial_index <= max_trials_per_path:
                result_path = _result_path(output_path, capability, path, trial_index)
                bundle_key = (capability.execution_id, trial_index)
                if bundle_key in bundle_packet_cache:
                    packet = bundle_packet_cache[bundle_key][path]
                elif result_path.exists():
                    if not resume:
                        raise ResultViolation(f"refusing to overwrite existing result packet: {result_path}")
                    packet = read_result_packet(result_path)
                    if packet.candidate_hash != exact_candidate:
                        raise ResultViolation("resume packet belongs to a different exact candidate")
                else:
                    cache_key = (capability.execution_id, path, trial_index)
                    executor = registry.get(capability.execution_id)
                    if executor is None:
                        outcome = ExecutionOutcome(
                            classification=ExecutionClassification.PENDING,
                            billing_classification="none",
                            reason_code="executor_not_registered",
                        )
                    elif cache_key in outcome_cache:
                        outcome = outcome_cache[cache_key]
                    else:
                        remaining_turn_budget = turn_budget - used_turns
                        context = ExecutionContext(
                            capability=capability,
                            path=path,
                            trial_index=trial_index,
                            profile_id=profile_id,
                            profile_hash=profile_hash,
                            plugin_sha=plugin_sha,
                            host_sha=host_sha,
                            sdk_version=sdk_version,
                            runner_version=runner_version,
                            inventory_hash=inventory_hash,
                            contract_hash=catalog.contract_hash,
                            catalog_hash=catalog.catalog_hash,
                            remaining_turn_budget=remaining_turn_budget,
                            repo_root=str(catalog.path.parent.parent),
                            inventory_tools=tuple(inventory_tools),
                            profile_isolation_kind=profile_isolation_kind,
                            profile_persistent=profile_persistent,
                            output_dir=str(output_path),
                        )
                        executor_result = await _call_executor(executor, context)
                        _validate_executor_result(executor_result, remaining_turn_budget)
                        _validate_runtime_campaign(
                            executor_result,
                            lane=lane,
                            execution_id=capability.execution_id,
                        )
                        execution_receipts.append(
                            _execution_receipt(
                                exact_candidate=exact_candidate,
                                execution_id=capability.execution_id,
                                path=None if isinstance(executor_result, ExecutionBundle) else path,
                                trial_index=trial_index,
                                result=executor_result,
                            )
                        )
                        used_turns += executor_result.turn_count
                        if isinstance(executor_result, ExecutionBundle):
                            built_packets: dict[str, ResultPacket] = {}
                            for bundle_path, bundle_outcome in executor_result.outcomes.items():
                                bundle_result_path = _result_path(
                                    output_path,
                                    capability,
                                    bundle_path,
                                    trial_index,
                                )
                                if bundle_result_path.exists():
                                    raise ResultViolation(
                                        "resume found an incomplete combined execution trial"
                                    )
                                bundle_packet = build_packet(
                                    capability,
                                    bundle_path,
                                    trial_index,
                                    bundle_outcome,
                                )
                                bundle_packet.write(bundle_result_path)
                                checkpoint_packets[bundle_result_path] = bundle_packet
                                built_packets[bundle_path] = bundle_packet
                            bundle_packet_cache[bundle_key] = built_packets
                            packet = built_packets[path]
                            checkpoint_manifest()
                            if any(
                                item.classification is ExecutionClassification.VERIFIED_FAILURE
                                for item in executor_result.outcomes.values()
                            ):
                                scenario_repeat_targets[capability.execution_id] = min(
                                    max_trials_per_path,
                                    max(
                                        scenario_repeat_targets.get(capability.execution_id, 1),
                                        trial_index + 3,
                                    ),
                                )
                                target = max(
                                    target,
                                    scenario_repeat_targets[capability.execution_id],
                                )
                        else:
                            outcome = executor_result
                            outcome_cache[cache_key] = outcome
                    if bundle_key not in bundle_packet_cache:
                        packet = build_packet(
                            capability,
                            path,
                            trial_index,
                            outcome,
                        )
                        packet.write(result_path)
                        checkpoint_packets[result_path] = packet
                        checkpoint_manifest()
                packets.append(packet)
                if packet.classification is ExecutionClassification.VERIFIED_FAILURE:
                    target = min(max_trials_per_path, max(target, trial_index + 3))
                if packet.classification in {
                    ExecutionClassification.PENDING,
                    ExecutionClassification.ENVIRONMENT_BLOCKED,
                }:
                    if packet.reason_code in global_environment_block_reasons:
                        abort_run = True
                    break
                trial_index += 1

    all_packet_paths = sorted(output_path.glob("*__*__trial-*.json"))
    all_packets = [read_result_packet(path) for path in all_packet_paths]
    checkpoint_manifest()

    # The report intentionally includes every lane capability. A selected thin
    # run therefore remains PENDING for the broader lane instead of overstating
    # release readiness.
    report = grade_packets(
        catalog,
        all_packets,
        lane=lane,
        expected_candidate_hash=exact_candidate,
    )
    return tuple(packets), report


def run_catalog(*args: Any, **kwargs: Any) -> tuple[tuple[ResultPacket, ...], GradeReport]:
    return asyncio.run(run_catalog_async(*args, **kwargs))


__all__ = [
    "ExecutionContext",
    "ExecutionBundle",
    "ExecutionOutcome",
    "Executor",
    "ExecutorRegistry",
    "load_entrypoint_executors",
    "run_catalog",
    "run_catalog_async",
    "validate_run_manifest",
]

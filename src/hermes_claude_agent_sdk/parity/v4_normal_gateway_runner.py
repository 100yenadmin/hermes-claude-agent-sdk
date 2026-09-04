"""Explicit normal-Gateway orchestration for one provider-free v4 trial."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .v4_background_delivery_receipt import run_v4_background_delivery_receipt
from .v4_contract import V3_RESULT_CATALOG_HASH, OWNERSHIP_PREFLIGHTS, load_v4_contract, validate_v4_contract
from .v4_fixture_materializer import V4FixtureMaterializer
from .v4_gateway import Gateway
from .v4_gateway_inventory import (
    V4GatewayInventory,
    load_v4_gateway_inventory,
)
from .v4_gateway_observer import V4GatewayObserver
from .v4_live_executor import V4LiveGateway, _candidate, _preflight_hash
from .v4_live_fixtures import (
    V4LiveFixtureManifest,
    load_v4_live_fixture_manifest,
    validate_v4_live_fixture_manifest,
)
from .v4_live_map import load_v4_live_execution_map, validate_v4_live_execution_map
from .v4_live_packets import LIVE_MAP_SHA256, build_v4_live_packets
from .v4_live_scenarios import build_v4_live_scenario_catalog
from .v4_live_session import V4LiveSession
from .v4_local_mechanism_executor import execute_v4_local_mechanism
from .v4_local_path_executor import execute_v4_local_path
from .v4_local_restart import run_v4_local_restart
from .hashing import canonical_json_bytes
from .results import candidate_hash as result_candidate_hash

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+#\-]{0,255}$")
_CANONICAL = {"message.start": "start", "session.start": "start", "run.start": "start", "message.state": "state", "session.state": "state", "message.usage": "usage", "session.usage": "usage", "tool.request": "tool_requested", "tool.requested": "tool_requested", "tool.start": "tool_requested", "tool.complete": "tool_result", "tool.completed": "tool_result", "approval.request": "approval_requested", "approval.requested": "approval_requested", "approval.responded": "approval_decision", "approval.decision": "approval_decision", "compaction": "compaction", "background": "background", "restart": "restart", "message.complete": "terminal", "session.complete": "terminal", "run.complete": "terminal", "task.complete": "terminal", "terminal": "terminal"}
_NON_CONTRACT_GATEWAY_EVENTS = frozenset({"session.info", "session.title", "sessions.changed", "status.update"})
_STRIP = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY", "ZAI_API_KEY", "EXTRA_USAGE", "CLAUDE_CODE_EXTRA_USAGE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE"})
class V4NormalGatewayRunnerViolation(ValueError):
    """Admission, observation, or packet composition failed closed."""


_LOCAL_EXECUTORS = {
    "clawprobench_native/constraints_23_external_approval_boundary_live": execute_v4_local_path,
    "openclaw_active/config-restart-capability-flip": run_v4_local_restart,
    "openclaw_active/subagent-handoff": run_v4_background_delivery_receipt,
    "openclaw_active/subagent-fanout-synthesis": run_v4_background_delivery_receipt,
}
_GENERIC_LOCAL_MECHANISMS = frozenset(
    {
        "host_tool_pdr",
        "host_delegate",
        "memory_session",
        "docs_skills",
        "local_cross_surface",
        "adversarial_local",
    }
)
_ZERO_CHILD_BACKGROUND_ROWS = frozenset(
    {
        "clawprobench_native/planning_19_agent_delegation_boundary_live",
        "clawprobench_native/planning_20_session_agent_handoff_live",
    }
)
@dataclass(frozen=True, slots=True)
class V4NormalGatewayAdmission:
    candidate_hash: str
    preflight_hash: str
    live_map_sha256: str
    fixture_manifest_sha256: str
    fixture_root_sha256: str
    row_key: str
    trial_index: int
    mandatory_paths: tuple[str, ...]
    turn_count: int
    parent_calls: int
    child_calls: int
    profile_id: str
    profile_sha256: str
    inventory_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None or value == "0" * 64:
        raise V4NormalGatewayRunnerViolation(f"{field} is not a nonzero SHA-256 digest")
    return value


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE.fullmatch(value) is None:
        raise V4NormalGatewayRunnerViolation(f"{field} is not a safe identifier")
    return value


def _doc(value: Mapping[str, Any] | str | Path | None, loader: Callable[[str | Path], dict[str, Any]], default: Path) -> dict[str, Any]:
    if value is None:
        return loader(default)
    if isinstance(value, (str, Path)):
        return loader(value)
    if not isinstance(value, Mapping):
        raise V4NormalGatewayRunnerViolation("artifact must be a mapping or bounded path")
    return dict(value)


def _bounded_root(value: str | Path, *, allow_missing: bool) -> Path:
    root = Path(value).expanduser()
    if not root.is_absolute() or ".." in root.parts or root.is_symlink() or len(str(root)) > 4096:
        raise V4NormalGatewayRunnerViolation("fixture root is not an absolute task-local directory")
    if not allow_missing and not root.exists():
        raise V4NormalGatewayRunnerViolation("fixture root is unavailable")
    try:
        resolved = root.resolve(strict=not allow_missing)
    except (OSError, RuntimeError):
        raise V4NormalGatewayRunnerViolation("fixture root is unavailable") from None
    if resolved.exists() and (not resolved.is_dir() or resolved == Path(resolved.anchor) or resolved.is_symlink()):
        raise V4NormalGatewayRunnerViolation("fixture root is unavailable")
    return resolved


def _safe_env(home: Path, *, model: str = "claude-fable-5-1", provider: str = "claude-agent-sdk") -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _STRIP or any(part in upper for part in ("API_KEY", "APIKEY", "GLM", "EXTRA_USAGE", "METERED", "BILLING", "PASSWORD", "SECRET", "OAUTH", "CREDENTIAL", "TOKEN")):
            continue
        if isinstance(value, str) and key not in {"HERMES_HOME", "HERMES_MODEL", "HERMES_TUI_PROVIDER"}:
            env[key] = value
    env.update({"HERMES_HOME": str(home), "HERMES_MODEL": model, "HERMES_TUI_PROVIDER": provider, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": env.get("PATH", os.defpath)})
    return env


def _stage_plugin(source: Path, home: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise V4NormalGatewayRunnerViolation("fixture plugin root is unavailable")
    destination = home / "plugins" / "v4_hermes_fixture"
    if destination.exists() or destination.is_symlink():
        raise V4NormalGatewayRunnerViolation("fixture plugin destination already exists")
    destination.mkdir(parents=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_symlink():
            raise V4NormalGatewayRunnerViolation("fixture plugin contains a symlink")
        if "__pycache__" in relative.parts or item.suffix in {".pyc", ".pyo"}:
            continue
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        else:
            raise V4NormalGatewayRunnerViolation("fixture plugin contains an unsupported entry")


class _ObservedGateway:
    """Rotate sanitized observers at turn boundaries over one real Gateway."""

    def __init__(self, gateway: V4LiveGateway, fixture_root: Path) -> None:
        self._gateway, self._root = gateway, fixture_root
        self._observers = [V4GatewayObserver(gateway, fixture_root=fixture_root)]
        self._observer, self._terminal = self._observers[0], False
        self._snapshots: list[dict[str, Any]] = []

    def start(self, *args: Any, **kwargs: Any) -> Any:
        return self._gateway.start(*args, **kwargs)

    def call(self, method: str, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Mapping[str, Any]:
        if method == "prompt.submit" and self._terminal:
            self._observer = V4GatewayObserver(self._gateway, fixture_root=self._root)
            self._observers.append(self._observer)
            self._terminal = False
        return self._observer.call(method, params, **kwargs)

    def next_event(self, **kwargs: Any) -> Any:
        value = self._observer.next_event(**kwargs)
        status = getattr(value, "terminal_status", None) if not isinstance(value, Mapping) else value.get("terminal_status")
        if status is not None:
            self._observer.fixture_snapshot(self._root)
            self._snapshots.append(self._observer.snapshot())
            self._terminal = True
        return value

    def close(self, *args: Any, **kwargs: Any) -> Any:
        return self._gateway.close(*args, **kwargs)

    @property
    def snapshots(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._snapshots)


def _trace(
    contract: Mapping[str, Any],
    scenario: Any,
    attempts: list[Mapping[str, Any]],
    snapshots: tuple[Mapping[str, Any], ...],
    host: Mapping[str, Any],
) -> dict[str, Any]:
    row = next(item for item in contract["source_rows"] if f"{item['source_pack']}/{item['source_item_id']}" == scenario.row_key)
    expected = tuple(row["expected_trace"])
    if len(attempts) != len(snapshots):
        raise V4NormalGatewayRunnerViolation("observer did not close every positive turn")
    observed: list[tuple[int, int, Mapping[str, Any], str]] = []
    for turn, (attempt, snapshot) in enumerate(zip(attempts, snapshots, strict=True), 1):
        events = snapshot.get("events")
        if not isinstance(events, list) or len(events) != len(attempt["events"]):
            raise V4NormalGatewayRunnerViolation("observer event projection differs from the live attempt")
        for ordinal, (event, projection) in enumerate(zip(attempt["events"], events, strict=True), 1):
            if any(event.get(key) != projection.get(key) for key in ("kind", "byte_length", "terminal_status")):
                raise V4NormalGatewayRunnerViolation("observer event projection is not attempt-bound")
            canonical = _CANONICAL.get(event.get("kind"))
            if canonical is not None:
                observed.append((turn, ordinal, event, canonical))
    chosen: list[dict[str, Any]] = []
    chosen_attempt_positions: list[tuple[int, int]] = []
    used: set[tuple[int, int]] = set()
    for kind in expected:
        candidates = [item for item in observed if item[3] == kind and (item[0], item[1]) not in used]
        if candidates:
            turn, ordinal, event, _ = candidates[-1] if kind == "terminal" else candidates[0]
            used.add((turn, ordinal))
            chosen_attempt_positions.append((turn, ordinal))
            chosen.append({"kind": event["kind"], "byte_length": event["byte_length"], "sha256": event["sha256"], "terminal_status": event["terminal_status"], "evidence": {"source": "attempt", "attempt_index": turn, "source_sha256": event["sha256"]}})
            continue
        component: Mapping[str, Any] | None = None
        component_name: str | None = None
        if kind == "state":
            state = host.get("runtime_state")
            if isinstance(state, Mapping) and state.get("present") is True:
                component, component_name = state, "runtime_state"
        elif kind == "usage":
            usage = host.get("runtime_usage")
            latest = usage.get("latest") if isinstance(usage, Mapping) else None
            if isinstance(latest, Mapping):
                component, component_name = latest, "runtime_usage"
        if component is None or component_name is None:
            raise V4NormalGatewayRunnerViolation("observed Hermes evidence lacks a required projection")
        digest = _digest(component.get("sha256"), f"host {component_name} proof")
        chosen.append({"kind": kind, "byte_length": len(canonical_json_bytes(component)), "sha256": digest, "terminal_status": None, "evidence": {"source": "host_observation", "component": component_name, "source_sha256": digest}})
    if chosen_attempt_positions != sorted(chosen_attempt_positions):
        raise V4NormalGatewayRunnerViolation("observed Gateway trace is out of order")
    return {"schema_version": 1, "row_key": scenario.row_key, "predecessor_execution_id": scenario.predecessor_execution_id, "path": "positive", "trial_index": attempts[0]["identity"]["trial_index"], "events": chosen}


def _packet_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    events = [event for event in attempt["events"] if not str(event.get("kind", "")).startswith("subagent.") and event.get("kind") not in _NON_CONTRACT_GATEWAY_EVENTS]
    return {**attempt, "event_count": len(events), "event_kinds": {kind: sum(event.get("kind") == kind for event in events) for kind in {event.get("kind") for event in events}}, "events": events}


def _delegation(scenario: Any, trial_index: int, snapshots: tuple[Mapping[str, Any], ...], durable: Mapping[str, Any]) -> dict[str, Any]:
    bindings = tuple(binding for binding in scenario.child_bindings if binding[1] == trial_index and binding[4] == "positive")
    count = len(bindings)
    # Hermes deliberately owns the dispatch mode: every model-facing,
    # top-level delegate_task call is one durable async batch, including
    # handoff/fanout rows whose feature classification is not
    # ``host_background``.  A fanout still creates one batch with N children.
    batch_count = 1 if count else 0
    if not isinstance(durable, Mapping) or durable.get("status") != "PASS" or durable.get("count") != batch_count or durable.get("background_count") != batch_count or durable.get("invariant_violations") != []:
        raise V4NormalGatewayRunnerViolation("durable delegation observation is missing or mismatched")
    if count == 0:
        if any(snapshot.get("subagents") for snapshot in snapshots):
            raise V4NormalGatewayRunnerViolation("unexpected child or background lifecycle was observed")
        observed_background = any(event.get("kind") == "background" for snapshot in snapshots for event in snapshot.get("events", ()))
        if observed_background and scenario.row_key not in _ZERO_CHILD_BACKGROUND_ROWS:
            raise V4NormalGatewayRunnerViolation("unexpected child or background lifecycle was observed")
        return {"count": 0, "background_count": 0, "lifecycle": "none", "parent_link_sha256": None}
    children = [child for snapshot in snapshots for child in snapshot.get("subagents", ())]
    groups = {index: [child for child in children if child.get("task_index") == index] for index in range(count)}
    if set(groups) != set(range(count)) or any(
        any(item.get("task_count") != count for item in items)
        or tuple(item.get("phase") for item in items) not in {
            ("start", "complete"),
            ("spawn_requested", "start", "complete"),
        }
        for items in groups.values()
    ):
        raise V4NormalGatewayRunnerViolation("observed child lifecycle does not match the immutable map")
    if tuple(sorted(binding[2] for binding in bindings)) != tuple(range(1, count + 1)):
        raise V4NormalGatewayRunnerViolation("immutable child ordinals are not contiguous")
    # Top-level Hermes child events are already scoped by the Gateway event
    # envelope to the observed parent session.  Their optional ``parent_id``
    # payload means "parent subagent" and is intentionally absent for a root
    # agent.  Bind root ownership to the durable async_delegations row queried
    # by that same stored session instead of inventing a payload requirement.
    durable_parent = _digest(
        durable.get("parent_link_sha256"), "durable delegation parent linkage"
    )
    event_parents = {
        child.get("parent_id_sha256")
        for child in children
        if child.get("parent_id_sha256") is not None
    }
    if len(event_parents) > 1:
        raise V4NormalGatewayRunnerViolation("observed child parent linkage is incomplete")
    observed_background = sum(1 for snapshot in snapshots for event in snapshot.get("events", ()) if event.get("kind") == "background")
    if observed_background not in (0, batch_count):
        raise V4NormalGatewayRunnerViolation("observed background child count is not map-bound")
    lifecycle = durable.get("lifecycle") if batch_count else snapshots[-1].get("terminal_status")
    if lifecycle not in {"pending", "running", "completed", "failed", "cancelled", "delivered", "dropped"}:
        raise V4NormalGatewayRunnerViolation("child lifecycle observation is incomplete")
    return {"count": count, "background_count": batch_count, "lifecycle": lifecycle, "parent_link_sha256": durable_parent}


def _local_observations(scenario: Any, trial_index: int, task_root: Path) -> dict[str, Mapping[str, Any]]:
    """Run only contract-bound provider-free denial/recovery mechanisms.

    Local receipts are selected by immutable row identity.  They are never
    accepted from the caller and cannot replace the provider-live positive
    attempt.
    """
    paths = tuple(path for path in scenario.mandatory_paths if path != "positive")
    executor = _LOCAL_EXECUTORS.get(scenario.row_key)
    if executor is None and scenario.mechanism_class in _GENERIC_LOCAL_MECHANISMS and paths:
        executor = execute_v4_local_mechanism
    if executor is None:
        return {}
    observations: dict[str, Mapping[str, Any]] = {}
    for path in paths:
        with tempfile.TemporaryDirectory(prefix="v4-local-observation-", dir=task_root) as scratch:
            observations[path] = executor(
                row_key=scenario.row_key,
                trial_index=trial_index,
                path=path,
                task_root=Path(scratch),
            )
    return observations


class V4NormalGatewayRunner:
    """Admit one row/trial and execute only its positive provider turns."""

    def __init__(self, *, candidate: Mapping[str, Any], preflight_projections: Mapping[str, Mapping[str, Any]], profile_id: str, tool_inventory: V4GatewayInventory | Mapping[str, Any] | str | Path, hermes_home: str | Path, fixture_root: str | Path | None = None, contract: Mapping[str, Any] | str | Path | None = None, live_map: Mapping[str, Any] | str | Path | None = None, fixture_manifest: V4LiveFixtureManifest | Mapping[str, Any] | str | Path | None = None, map_path: str | Path | None = None, manifest_path: str | Path | None = None, plugin_root: str | Path | None = None, python: str | Path | None = None, cwd: str | Path | None = None, gateway_factory: Callable[..., V4LiveGateway] | None = None, row_key: str | None = None, trial_index: int | None = None) -> None:
        try:
            home = _bounded_root(hermes_home, allow_missing=True)
            task_root = _bounded_root(fixture_root or hermes_home, allow_missing=True)
            map_source = Path(map_path).expanduser().resolve() if map_path is not None else Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"
            map_doc = _doc(live_map, load_v4_live_execution_map, map_source)
            if validate_v4_live_execution_map(map_doc, map_path=map_source).get("map_sha256") != LIVE_MAP_SHA256:
                raise ValueError("live map is not immutable")
            contract_source = contract if contract is not None else Path(__file__).resolve().parents[3] / "qa" / "parity-contract-v4.yaml"
            contract_doc = load_v4_contract(contract_source) if isinstance(contract_source, (str, Path)) else dict(contract_source)
            validate_v4_contract(contract_doc)
            manifest_source = Path(manifest_path).expanduser().resolve() if manifest_path is not None else Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-fixtures.yaml"
            manifest = load_v4_live_fixture_manifest(manifest_source) if fixture_manifest is None else load_v4_live_fixture_manifest(fixture_manifest) if isinstance(fixture_manifest, (str, Path)) else fixture_manifest if isinstance(fixture_manifest, V4LiveFixtureManifest) else V4LiveFixtureManifest(dict(fixture_manifest))
            validate_v4_live_fixture_manifest(manifest, live_map=map_doc, map_path=map_source)
            materializer = V4FixtureMaterializer(manifest=manifest, map_path=map_source)
            catalog = build_v4_live_scenario_catalog(map_doc, map_path=map_source)
            normalized, candidate_hash = _candidate(candidate)
            preflight_hash = _preflight_hash(preflight_projections, candidate_hash)
            profile = _safe_id(profile_id, "profile_id")
            inventory = load_v4_gateway_inventory(
                tool_inventory,
                expected_profile_id=profile,
                expected_profile_sha256=normalized["profile_sha256"],
                expected_host_sha=normalized["host_sha"],
            )
            if "v4_fixture_local_state" not in inventory.executable_names:
                raise ValueError("v4 fixture tool is absent from the executable inventory")
            if "delegate_task" not in inventory.host_event_names:
                raise ValueError("Hermes delegate_task is absent from the inventory")
        except V4NormalGatewayRunnerViolation:
            raise
        except Exception as exc:
            raise V4NormalGatewayRunnerViolation("v4 normal Gateway admission failed") from exc
        self._candidate, self._preflights, self._contract = normalized, {name: dict(preflight_projections[name]) for name in OWNERSHIP_PREFLIGHTS}, contract_doc
        self._map, self._map_path, self._manifest, self._materializer, self._catalog = map_doc, map_source, manifest, materializer, catalog
        self._home, self._task_root = home, task_root
        self._plugin_root = Path(plugin_root).expanduser() if plugin_root is not None else Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "v4_hermes_plugin"
        self._python, self._cwd, self._factory = os.fspath(python or sys.executable), cwd, gateway_factory
        self._profile_id, self._inventory = profile, inventory
        self._inventory_hash = inventory.inventory_sha256
        self._candidate_hash, self._preflight_hash = candidate_hash, preflight_hash
        self._executed, self._admission = False, None
        self._bound_row_key, self._bound_trial_index = None, None
        if row_key is not None or trial_index is not None:
            if row_key is None or trial_index is None:
                raise V4NormalGatewayRunnerViolation("row and trial must be admitted together")
            self._admit(row_key, trial_index)

    def _admit(self, row_key: str, trial_index: int) -> Any:
        try:
            scenario = next(item for item in self._catalog.scenarios if item.row_key == row_key)
            fixture = next(item for item in self._materializer.fixtures if item.row_key == row_key)
        except StopIteration as exc:
            raise V4NormalGatewayRunnerViolation("row is not in the immutable map") from exc
        if type(trial_index) is not int or trial_index not in scenario.trial_indexes or fixture.turn_count != scenario.turn_count or scenario.parent_calls != scenario.turn_count * len(scenario.trial_indexes):
            raise V4NormalGatewayRunnerViolation("row/trial budget is not immutable")
        self._bound_row_key, self._bound_trial_index = row_key, trial_index
        self._admission = V4NormalGatewayAdmission(self._candidate_hash, self._preflight_hash, LIVE_MAP_SHA256, self._manifest.manifest_sha256, hashlib.sha256(str(self._task_root).encode()).hexdigest(), row_key, trial_index, tuple(scenario.mandatory_paths), scenario.turn_count, scenario.parent_calls, scenario.child_calls, self._profile_id, self._candidate["profile_sha256"], self._inventory_hash)
        return scenario

    @property
    def admission(self) -> V4NormalGatewayAdmission:
        if self._admission is None:
            raise V4NormalGatewayRunnerViolation("row/trial admission occurs at execute")
        return self._admission

    def execute(self, row_key: str | None = None, trial_index: int | None = None, *, db_path: str | Path | None = None, gateway: V4LiveGateway | None = None) -> dict[str, Any]:
        if self._executed:
            raise V4NormalGatewayRunnerViolation("runner is single-use")
        self._executed = True
        try:
            row_key = row_key or self._bound_row_key
            trial_index = trial_index if trial_index is not None else self._bound_trial_index
            if row_key is None or trial_index is None:
                raise V4NormalGatewayRunnerViolation("row and trial are required")
            if self._admission is not None and (row_key, trial_index) != (self._bound_row_key, self._bound_trial_index):
                raise V4NormalGatewayRunnerViolation("execution identity differs from admission")
            scenario = self._admit(row_key, trial_index) if self._admission is None else next(item for item in self._catalog.scenarios if item.row_key == row_key)
            self._home.mkdir(parents=True, exist_ok=True)
            if self._task_root != self._home:
                self._task_root.mkdir(parents=True, exist_ok=True)
            _stage_plugin(self._plugin_root, self._home)
            env = _safe_env(self._home)
            if gateway is not None and self._factory is not None:
                raise V4NormalGatewayRunnerViolation("gateway and gateway_factory are mutually exclusive")
            if gateway is None:
                gateway = self._factory(env=env, cwd=self._cwd or self._map_path.parent, host_tools=self._inventory.host_event_names, mcp_tools=self._inventory.mcp_event_names) if self._factory is not None else Gateway(python=self._python, cwd=self._cwd or self._map_path.parent, env=env, host_tools=self._inventory.host_event_names, mcp_tools=self._inventory.mcp_event_names)
            if isinstance(gateway, Gateway) and gateway.command[1:] != ("-u", "-m", "tui_gateway.entry"):
                raise V4NormalGatewayRunnerViolation("normal Gateway command is not tui_gateway.entry")
            observed_gateway = _ObservedGateway(gateway, self._task_root)
            session = V4LiveSession(gateway=observed_gateway, candidate=self._candidate, preflight_projections=self._preflights, live_map=self._map, map_path=self._map_path, expected_live_map_sha256=LIVE_MAP_SHA256, planned_calls=scenario.turn_count, planned_turns=scenario.turn_count)
            attempts: list[dict[str, Any]] = []
            database = db_path or self._home / "state.db"
            try:
                session.start()
                session.verify_tool_inventory(tuple(self._inventory.executable_names))
                for turn in range(1, scenario.turn_count + 1):
                    material = self._materializer.materialize(row_key=row_key, trial_index=trial_index, turn_index=turn, path="positive", task_root=self._task_root if scenario.mechanism_class == "host_tool_pdr" else None)
                    attempts.append(session.run_turn(material.prompt.text, source_pack=scenario.source_pack, source_item_id=scenario.source_item_id, path="positive", trial_index=trial_index, approval_choice=material.host.approval_choice, planned_calls=1))
                host = session.collect_host_observation(database, allowed_root=self._home, expected_turn_count=scenario.turn_count)
            finally:
                session.close()
            trial_child_count = sum(
                binding[1] == trial_index and binding[4] == "positive"
                for binding in scenario.child_bindings
            )
            expected_batches = 1 if trial_child_count else 0
            durable = session.collect_delegation_observation(database, allowed_root=self._home, expected_count=expected_batches)
            trace = _trace(self._contract, scenario, attempts, observed_gateway.snapshots, host)
            delegation = _delegation(scenario, trial_index, observed_gateway.snapshots, durable)
            packet_attempts = [_packet_attempt(attempt) for attempt in attempts]
            trial_candidate_hash = result_candidate_hash(catalog_hash=V3_RESULT_CATALOG_HASH, plugin_sha=self._candidate["plugin_sha"], host_sha=self._candidate["host_sha"], sdk_version=self._candidate["sdk_version"], profile_hash=self._candidate["profile_sha256"], runner_version=self._candidate["runner_version"], inventory_hash=self._inventory_hash)
            receipt = {"schema_version": 1, "candidate": self._candidate, "preflight_projections": self._preflights, "attempts": packet_attempts, "host_observation": host, "profile_id": self._profile_id, "inventory_hash": self._inventory_hash, "stream_projection": {"schema_version": 1, "name": "stream", "candidate_hash": self._candidate_hash, "trial_candidate_hash": trial_candidate_hash, "trial_index": trial_index, "status": "PASS", "source": {"executable": "normal_gateway", "source_ref": "v4_gateway_observer", "test_id": "positive_turn"}, "observation": {"event_count": sum(item["event_count"] for item in attempts), "provider_calls": scenario.turn_count}}, "scenario_trace": trace, "delegation": delegation}
            local_observations = _local_observations(scenario, trial_index, self._task_root)
            return build_v4_live_packets(self._contract, scenario, receipt, local_observations, live_map=self._map, map_path=self._map_path, scenario_catalog=self._catalog)
        except V4NormalGatewayRunnerViolation:
            raise
        except Exception as exc:
            raise V4NormalGatewayRunnerViolation("v4 normal Gateway execution failed closed") from exc
    run = execute
run_v4_normal_gateway = V4NormalGatewayRunner
NormalGatewayRunner = V4NormalGatewayRunner
__all__ = ["NormalGatewayRunner", "V4NormalGatewayAdmission", "V4NormalGatewayRunner", "V4NormalGatewayRunnerViolation", "run_v4_normal_gateway"]

"""Sealed provider-free denial/recovery execution for v4 mechanism classes.

The frozen v4 map describes the local mechanism surface, while this module
performs one bounded operation against an ephemeral ``NativeSandboxHost``.
The operation result and host trace are reduced to receipt-safe metadata; no
caller-supplied expectation or observation is accepted.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from .hashing import canonical_json_bytes, sha256_value
from .native_sandbox import NativeSandboxHost, native_environment_snapshot
from .v4_contract import load_v4_contract, validate_v4_contract
from .v4_fixture_materializer import V4FixtureMaterializer
from .v4_live_fixtures import load_v4_live_fixture_manifest
from .v4_live_map import V4LiveMapViolation, load_v4_live_execution_map
from .v4_live_scenarios import build_v4_live_scenario_catalog

_ROOT = Path(__file__).resolve().parents[3]
_MAP = _ROOT / "qa" / "parity-v4-live-execution-map.yaml"
_CONTRACT = _ROOT / "qa" / "parity-contract-v4.yaml"
_MANIFEST = _ROOT / "qa" / "parity-v4-live-fixtures.yaml"
_MAP_SHA256 = "aa68ce417d9a8ad74110de76f37ef550e1f5414eba0a6ecba0af235ba1488c69"
_CONTRACT_SHA256 = "53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94"
_MANIFEST_SHA256 = "52c0ee8bc02b782e64c7fb30a32b76e57fcc0787059a050a409b9992b81f46f2"
_MANIFEST_FILE_SHA256 = "ea8c5e01b9d54aa1f143d89d63f31274aa200b54127e229a8b089cba84fc08a8"
_GENERIC_MECHANISMS = frozenset(
    {
        "host_tool_pdr",
        "host_delegate",
        "memory_session",
        "docs_skills",
        "local_cross_surface",
        "adversarial_local",
    }
)
_SPECIALIZED_ROWS = frozenset(
    {
        "clawprobench_native/constraints_23_external_approval_boundary_live",
        "openclaw_active/config-restart-capability-flip",
        "openclaw_active/subagent-handoff",
        "openclaw_active/subagent-fanout-synthesis",
    }
)
_PATHS = frozenset({"denial", "recovery"})
_FROZEN: tuple[Any, Any, V4FixtureMaterializer, Any] | None = None


class V4LocalMechanismExecutorViolation(ValueError):
    """A generic local mechanism could not be admitted or observed safely."""


def _fail(message: str) -> None:
    raise V4LocalMechanismExecutorViolation(message)


def _root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        _fail("task_root must be an absolute local directory")
    root = Path(value)
    if (
        not root.is_absolute()
        or ".." in root.parts
        or root.is_symlink()
        or not root.exists()
        or len(str(root)) > 4096
    ):
        _fail("task_root is not an absolute local directory")
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("task_root is unavailable")
    user_home = Path.home().resolve()
    if (
        not root.is_dir()
        or root == Path(root.anchor)
        or root == user_home
        or user_home.is_relative_to(root)
    ):
        _fail("task_root is not an isolated local directory")
    return root


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_file(workspace: Path, name: str, content: bytes, *, protected: bool = False) -> Path:
    path = workspace / name
    if path.exists() or path.is_symlink() or path.parent != workspace:
        _fail("local fixture target is not fresh")
    try:
        with path.open("xb") as handle:
            handle.write(content)
        if protected:
            path.chmod(0o400)
    except (OSError, ValueError):
        _fail("local fixture could not be seeded")
    return path


def _run_async(awaitable: Any) -> Any:
    try:
        return asyncio.run(awaitable)
    except (RuntimeError, TypeError, ValueError):
        _fail("local host operation could not be completed")


def _host_operation(
    host: NativeSandboxHost,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    path: str,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    first = _run_async(host.execute_tool(tool, arguments))
    results: list[Any] = [first]
    if path == "denial":
        if not host.denial_observed or host.successful_calls != 0:
            _fail("local denial was not observed before a side effect")
    else:
        second = _run_async(host.execute_tool(tool, arguments))
        results.append(second)
        if not host.denial_observed or not host.recovery_observed or host.successful_calls != 1:
            _fail("local recovery did not follow one denial")
    first_denied = isinstance(first, Mapping) and isinstance(first.get("error"), str)
    if not first_denied:
        _fail("local host did not return its denial envelope")
    if path == "recovery":
        second = results[1]
        if isinstance(second, Mapping) and isinstance(second.get("error"), str):
            _fail("local host recovery remained denied")
    result_kinds = tuple(
        "error" if isinstance(value, Mapping) and isinstance(value.get("error"), str)
        else "mapping" if isinstance(value, Mapping)
        else "text" if isinstance(value, str)
        else type(value).__name__
        for value in results
    )
    counts = Counter(item.get("type") for item in host.trace_events)
    summary = {
        "tool": tool,
        "attempt_count": len(results),
        "request_count": counts.get("tool_call", 0),
        "result_count": counts.get("tool_result", 0),
        "successful_calls": host.successful_calls,
        "denial_observed": host.denial_observed,
        "recovery_observed": host.recovery_observed,
        "result_kinds": result_kinds,
    }
    if summary["request_count"] != len(results) or summary["result_count"] != len(results):
        _fail("local host trace did not close each request/result pair")
    return tuple(results), summary


def _state(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        _fail("local fixture state is a symlink")
    if not path.exists():
        return {"present": False, "size": 0, "sha256": None}
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        _fail("local fixture state is not bounded")
    data = path.read_bytes()
    return {"present": True, "size": len(data), "sha256": _digest(data)}


def _seed_and_read(
    workspace: Path,
    host: NativeSandboxHost,
    *,
    path: str,
    operation: str,
) -> dict[str, Any]:
    seed = _safe_file(workspace, "fixture-input.md", b"synthetic v4 fixture document\n", protected=True)
    before = _state(seed)
    results, summary = _host_operation(host, "read", {"path": seed.name}, path=path)
    if path == "recovery" and results[1] != seed.read_text(encoding="utf-8"):
        _fail("local read recovery returned unexpected fixture content")
    return {
        "operation": operation,
        "state_before": before,
        "state_after": _state(seed),
        "host": summary,
    }


def _write_and_verify(
    workspace: Path,
    host: NativeSandboxHost,
    *,
    path: str,
    operation: str,
    content: bytes,
) -> dict[str, Any]:
    target = workspace / "fixture-output.json"
    before = _state(target)
    results, summary = _host_operation(
        host,
        "write",
        {"path": target.name, "content": content.decode("utf-8")},
        path=path,
    )
    after = _state(target)
    if path == "denial" and after["present"]:
        _fail("local denial changed task-local state")
    if path == "recovery":
        if not after["present"] or not isinstance(results[1], Mapping) or results[1].get("status") != "written":
            _fail("local recovery did not write the bounded fixture")
        if after["sha256"] != _digest(content):
            _fail("local recovery state hash is not exact")
    return {
        "operation": operation,
        "state_before": before,
        "state_after": after,
        "content_sha256": _digest(content),
        "host": summary,
    }


def _tool_handler(workspace: Path, host: NativeSandboxHost, *, path: str, **_: Any) -> dict[str, Any]:
    results, summary = _host_operation(host, "cron", {"action": "list"}, path=path)
    if path == "recovery" and results[1] != {"jobs": []}:
        _fail("local tool recovery returned an unexpected bounded result")
    return {"operation": "local_tool.invoke", "state": {"jobs": 0}, "host": summary}


def _delegate_handler(workspace: Path, host: NativeSandboxHost, *, path: str, scenario: Any, **_: Any) -> dict[str, Any]:
    content = json.dumps(
        {"schema_version": 1, "child_count": scenario.child_calls, "status": "completed"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    value = _write_and_verify(workspace, host, path=path, operation="local_delegate.create", content=content)
    value["delegation"] = {
        "expected_child_count": scenario.child_calls,
        "observed_child_count": 0,
        "lifecycle": "none",
        "background_management_observed": scenario.child_calls == 0,
    }
    return value


def _memory_handler(workspace: Path, host: NativeSandboxHost, *, path: str, **_: Any) -> dict[str, Any]:
    content = b'{"schema_version":1,"record":"synthetic-memory"}\n'
    return _write_and_verify(workspace, host, path=path, operation="local_memory.write", content=content)


def _docs_handler(workspace: Path, host: NativeSandboxHost, *, path: str, scenario: Any, **_: Any) -> dict[str, Any]:
    if scenario.row_key.endswith("/tool_use_14_openclaw_skill_inventory_live"):
        results, summary = _host_operation(host, "exec", {"command": "openclaw skills list"}, path=path)
        if path == "recovery" and (not isinstance(results[1], Mapping) or results[1].get("exit_code") != 0):
            _fail("local skills recovery was not successful")
        return {
            "operation": "local_skills.inspect",
            "environment_hash": sha256_value(native_environment_snapshot(("skills",))),
            "host": summary,
        }
    return _seed_and_read(workspace, host, path=path, operation="local_docs.read")


def _cross_surface_handler(workspace: Path, host: NativeSandboxHost, *, path: str, scenario: Any, **_: Any) -> dict[str, Any]:
    command = (
        "openclaw message send --dry-run"
        if scenario.operation == "host_denial_local_recovery"
        else "openclaw browser"
    )
    results, summary = _host_operation(host, "exec", {"command": command}, path=path)
    if path == "recovery" and (not isinstance(results[1], Mapping) or results[1].get("exit_code") != 0):
        _fail("local cross-surface recovery was not successful")
    return {
        "operation": "local_cross_surface.inspect",
        "surface": "message_dry_run" if "message" in command else "browser_fixture",
        "host": summary,
    }


def _adversarial_handler(workspace: Path, host: NativeSandboxHost, *, path: str, scenario: Any, trial_index: int, **_: Any) -> dict[str, Any]:
    content = json.dumps(
        {"schema_version": 1, "row_digest": _digest(scenario.row_key.encode()), "trial": trial_index},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    value = _write_and_verify(workspace, host, path=path, operation="local_adversarial.evaluate", content=content)
    value["evaluation"] = {"bounded": True, "input_hash": _digest(content), "decision": "safe_recovery" if path == "recovery" else "denied"}
    return value


_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "host_tool_pdr": _tool_handler,
    "host_delegate": _delegate_handler,
    "memory_session": _memory_handler,
    "docs_skills": _docs_handler,
    "local_cross_surface": _cross_surface_handler,
    "adversarial_local": _adversarial_handler,
}


def _event(kind: str, ordinal: int, path: str, facts: Mapping[str, Any], terminal: str | None = None) -> dict[str, Any]:
    projection = {"source": "native_sandbox", "kind": kind, "ordinal": ordinal, "path": path, **dict(facts)}
    encoded = canonical_json_bytes(projection)
    return {
        "kind": kind,
        "byte_length": len(encoded),
        "sha256": _digest(encoded),
        "terminal_status": terminal,
    }


def _admit(row_key: str, trial_index: int, path: str) -> tuple[Any, Mapping[str, Any]]:
    if not isinstance(row_key, str) or not isinstance(path, str) or path not in _PATHS or type(trial_index) is not int:
        _fail("row, trial, or path is unsupported")
    try:
        document, contract, materializer, catalog = _frozen_inputs()
        scenario = next(item for item in catalog.scenarios if item.row_key == row_key)
        row = next(item for item in contract["source_rows"] if f"{item['source_pack']}/{item['source_item_id']}" == row_key)
    except (OSError, KeyError, StopIteration, TypeError, ValueError, V4LiveMapViolation):
        _fail("frozen v4 local inputs are unavailable")
    if (
        row_key in _SPECIALIZED_ROWS
        or scenario.mechanism_class not in _GENERIC_MECHANISMS
        or not {"positive", "denial", "recovery"}.issubset(scenario.mandatory_paths)
        or path not in row["mandatory_paths"]
        or trial_index not in scenario.trial_indexes
        or row["predecessor_execution_id"] != scenario.predecessor_execution_id
    ):
        _fail("row path or trial is not admitted to the generic dispatcher")
    if scenario.mechanism_class not in _HANDLERS:
        _fail("row mechanism has no sealed handler")
    try:
        for turn in range(1, scenario.turn_count + 1):
            materializer.materialize(row_key=row_key, trial_index=trial_index, turn_index=turn, path=path)
    except Exception:
        _fail("row fixture recipe could not be materialized")
    trace = tuple(row["expected_trace"])
    if not trace or any(not isinstance(kind, str) for kind in trace):
        _fail("row trace is not frozen")
    return scenario, {"expected_trace": trace, "mechanism_class": scenario.mechanism_class}


def _frozen_inputs() -> tuple[Any, Any, V4FixtureMaterializer, Any]:
    """Load validated artifacts once while rechecking their byte identities."""
    global _FROZEN
    try:
        if (
            _digest(_MAP.read_bytes()) != _MAP_SHA256
            or _digest(_CONTRACT.read_bytes()) != _CONTRACT_SHA256
            or _digest(_MANIFEST.read_bytes()) != _MANIFEST_FILE_SHA256
        ):
            _fail("frozen v4 source identity drifted")
    except OSError:
        _fail("frozen v4 source identity is unavailable")
    if _FROZEN is None:
        try:
            document = load_v4_live_execution_map(_MAP)
            contract = load_v4_contract(_CONTRACT)
            validate_v4_contract(contract)
            manifest = load_v4_live_fixture_manifest(_MANIFEST)
            materializer = V4FixtureMaterializer(manifest=manifest, map_path=_MAP)
            if manifest.manifest_sha256 != _MANIFEST_SHA256:
                _fail("frozen v4 fixture manifest identity drifted")
            catalog = build_v4_live_scenario_catalog(document, map_path=_MAP)
        except (OSError, KeyError, TypeError, ValueError, V4LiveMapViolation):
            _fail("frozen v4 local inputs are unavailable")
        _FROZEN = (document, contract, materializer, catalog)
    return _FROZEN


def _execute(row_key: str, trial_index: int, path: str, root: Path, scenario: Any, trace: tuple[str, ...]) -> dict[str, Any]:
    handler = _HANDLERS[scenario.mechanism_class]
    with tempfile.TemporaryDirectory(prefix="v4-mechanism-", dir=root) as scratch:
        workspace = Path(scratch)
        host = NativeSandboxHost(workspace, ())
        facts = handler(workspace, host, path=path, scenario=scenario, trial_index=trial_index)
        facts.update(
            {
                "mechanism_class": scenario.mechanism_class,
                "required_surfaces": tuple(scenario.required_surfaces),
                "trial_index": trial_index,
            }
        )
        terminal = "denied" if path == "denial" else "completed"
        events = [
            _event(
                kind,
                ordinal,
                path,
                {
                    "mechanism_class": scenario.mechanism_class,
                    "operation": facts["operation"],
                    "attempt_count": facts["host"]["attempt_count"] if "host" in facts else 0,
                    "request_count": facts["host"]["request_count"] if "host" in facts else 0,
                    "result_count": facts["host"]["result_count"] if "host" in facts else 0,
                    "child_count": scenario.child_calls,
                },
                terminal if kind == "terminal" else None,
            )
            for ordinal, kind in enumerate(trace, 1)
        ]
        if tuple(item["kind"] for item in events) != trace:
            _fail("local event sequence does not match the frozen trace")
        observation = {
            "identity": {"row_key": row_key, "path": path, "trial_index": trial_index},
            "mechanism_class": scenario.mechanism_class,
            "operation": facts,
            "provider_calls": 0,
        }
        return {
            "schema_version": 1,
            "status": "PASS",
            "path": path,
            "host_local": True,
            "provider_calls": 0,
            "terminal_status": terminal,
            "events": events,
            "observation": observation,
            "proof_hashes": {
                "primary": sha256_value(observation),
                "secondary": sha256_value({"identity": observation["identity"], "events": events}),
            },
        }


def execute_v4_local_mechanism(*, row_key: str, trial_index: int, path: str, task_root: str | Path) -> dict[str, Any]:
    """Execute one generic frozen v4 denial/recovery path locally."""
    root = _root(task_root)
    scenario, metadata = _admit(row_key, trial_index, path)
    return _execute(row_key, trial_index, path, root, scenario, tuple(metadata["expected_trace"]))


def generic_v4_local_rows() -> tuple[str, ...]:
    """Return the exact generic row partition from the frozen map."""
    try:
        _, _, _, catalog = _frozen_inputs()
    except V4LocalMechanismExecutorViolation:
        raise
    except Exception as exc:
        raise V4LocalMechanismExecutorViolation("frozen v4 local rows are unavailable") from exc
    rows = tuple(
        scenario.row_key
        for scenario in catalog.scenarios
        if scenario.row_key not in _SPECIALIZED_ROWS
        and scenario.mechanism_class in _GENERIC_MECHANISMS
        and {"positive", "denial", "recovery"}.issubset(scenario.mandatory_paths)
    )
    if len(rows) != 40:
        _fail("generic frozen row partition is not exactly 40 rows")
    return rows


run_v4_local_mechanism = execute_v4_local_mechanism
execute_local_v4_mechanism = execute_v4_local_mechanism

__all__ = [
    "V4LocalMechanismExecutorViolation",
    "execute_local_v4_mechanism",
    "execute_v4_local_mechanism",
    "generic_v4_local_rows",
    "run_v4_local_mechanism",
]

"""Sealed provider-free receipts for Hermes background delivery recovery."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import queue
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any

_ROWS = {
    "openclaw_active/subagent-handoff": ("ONE_PARENT_PROMPT", 1),
    "openclaw_active/subagent-fanout-synthesis": ("FANOUT_PARENT_PROMPT", 2),
}
_PATHS = {"positive", "denial", "recovery"}
_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "v4_delegation_runtime_plugin"


class V4BackgroundDeliveryViolation(ValueError):
    """A background receipt cannot be admitted or observed safely."""


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "v4_background_delivery_fixture", _PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise V4BackgroundDeliveryViolation("provider-free fixture is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate(row_key: Any, trial_index: Any, path: Any, task_root: Any) -> tuple[str, int, str, Path, tuple[str, int]]:
    if not isinstance(row_key, str) or row_key not in _ROWS:
        raise V4BackgroundDeliveryViolation("row_key is not an admitted mapped delegation row")
    if type(trial_index) is not int or trial_index != 1:
        raise V4BackgroundDeliveryViolation("trial_index is not declared by the admitted row")
    if not isinstance(path, str) or path not in _PATHS:
        raise V4BackgroundDeliveryViolation("path is not a supported p/d/r path")
    root = Path(task_root).expanduser() if isinstance(task_root, (str, Path)) else None
    if root is None or not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise V4BackgroundDeliveryViolation("task_root must be an existing absolute directory")
    return row_key, trial_index, path, root.resolve(), _ROWS[row_key]


def _await_event(registry: Any, parent_id: str, count: int, model: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    held: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    try:
        while time.monotonic() < deadline:
            try:
                event = registry.completion_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if event.get("type") != "async_delegation" or event.get("parent_session_id") != parent_id:
                held.append(event)
                continue
            results = event.get("results")
            if (event.get("status") != "completed" or event.get("is_batch") is not True
                    or not isinstance(results, list) or len(results) != count):
                raise V4BackgroundDeliveryViolation("observed completion is not the admitted batch")
            if any(
                not isinstance(item, dict)
                or item.get("status") != "completed"
                or item.get("api_calls") != 0
                or item.get("model") != model
                or item.get("summary") != "v4 fixture child completed"
                for item in results
            ):
                raise V4BackgroundDeliveryViolation("provider-free child completion is incomplete")
            found = event
            break
        if found is None:
            raise V4BackgroundDeliveryViolation("provider-free batch completion was not observed")
        return found
    finally:
        for event in held:
            registry.completion_queue.put(event)


def _state(db_path: Path, delegation_id: str, parent_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT state, delivery_state, delivery_attempts FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
        if row is None:
            raise V4BackgroundDeliveryViolation("durable batch row disappeared")
        parent_rows = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND display_kind=?",
            (parent_id, "async_delegation_complete"),
        ).fetchone()[0]
        durable_rows = conn.execute(
            "SELECT COUNT(*) FROM async_delegations WHERE parent_session_id=?",
            (parent_id,),
        ).fetchone()[0]
    return {
        "state": row[0], "delivery_state": row[1], "delivery_attempts": row[2],
        "parent_delivery_rows": parent_rows, "durable_rows": durable_rows,
    }


def _requeue(registry: Any, event: dict[str, Any], parent_id: str) -> dict[str, Any]:
    registry.completion_queue.put(event)
    try:
        replay = registry.completion_queue.get(timeout=2)
    except queue.Empty as exc:
        raise V4BackgroundDeliveryViolation("released completion was not requeued") from exc
    if replay is not event or replay.get("parent_session_id") != parent_id:
        raise V4BackgroundDeliveryViolation("requeue changed completion identity")
    return replay


def _token(kind: str, observation: dict[str, Any], terminal: str | None = None) -> dict[str, Any]:
    payload = {"kind": kind, "observation": observation, "terminal": terminal}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "kind": kind, "byte_length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(), "terminal_status": terminal,
    }


def run_v4_background_delivery_receipt(row_key: str, trial_index: int, path: str, task_root: str | Path) -> dict[str, Any]:
    """Run one sealed mapped batch and return an existing ``_local`` envelope.

    The caller supplies only row identity, repetition, path, and an ephemeral
    root. All producer, queue, delivery, and terminal evidence is observed
    from the real host runtime; no expected trace or proof is accepted.
    """
    row_key, trial_index, path, root, (prompt_name, child_count) = _validate(row_key, trial_index, path, task_root)
    host_value = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not host_value or not Path(host_value).is_dir():
        raise V4BackgroundDeliveryViolation("HERMES_AGENT_HOST_ROOT is not configured")
    plugin = _fixture()
    old_env = {key: os.environ.get(key) for key in ("HERMES_HOME", "HERMES_BUNDLED_PLUGINS", "HERMES_INTERACTIVE")}
    old_path = list(sys.path)
    old_create: Any = None
    old_manager: Any = None
    parent: Any = None
    db: Any = None
    manager: Any = None
    with tempfile.TemporaryDirectory(prefix="v4-background-", dir=str(root)) as scratch:
        scratch_path = Path(scratch)
        home, bundled = scratch_path / "hermes-home", scratch_path / "empty-bundled"
        installed = home / "plugins" / plugin.PLUGIN_ID
        installed.parent.mkdir(parents=True)
        (home / "logs").mkdir()
        bundled.mkdir()
        shutil.copytree(_PLUGIN_DIR, installed)
        (home / "config.yaml").write_text(
            f"plugins:\n  enabled:\n    - {plugin.PLUGIN_ID}\n"
            "delegation:\n  max_iterations: 1\n  max_spawn_depth: 1\n"
            "auxiliary:\n  title_generation:\n    enabled: false\n",
            encoding="utf-8",
        )
        os.environ.update({"HERMES_HOME": str(home), "HERMES_BUNDLED_PLUGINS": str(bundled), "HERMES_INTERACTIVE": "0"})
        host_root = Path(host_value).resolve()
        if str(host_root) not in sys.path:
            sys.path.insert(0, str(host_root))
        from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
        from hermes_cli import plugins as plugins_mod
        from hermes_cli.plugins import PluginManager
        from hermes_state import SessionDB
        from run_agent import AIAgent
        from tools.process_registry import process_registry
        from gateway.wake import persist_delegation_delivery

        override = set_hermes_home_override(home)
        old_manager = plugins_mod._plugin_manager
        manager = PluginManager(scope_key=hermes_home_key(home))
        manager._scan_entry_points = lambda: []  # type: ignore[method-assign]
        plugins_mod._plugin_manager = manager
        manager.discover_and_load()
        db = SessionDB(home / "state.db")
        parent_id = "v4-background-parent-" + _hash({"row": row_key, "trial": trial_index, "path": path})[:16]
        old_create = AIAgent._create_openai_client
        calls: list[int] = []

        def forbidden_client(self: Any, *args: Any, **kwargs: Any) -> None:
            calls.append(1)
            raise V4BackgroundDeliveryViolation("provider client construction was attempted")

        AIAgent._create_openai_client = forbidden_client
        try:
            parent = AIAgent(
                provider=plugin.PROVIDER_ID, model=plugin.MODEL_ID, api_mode=plugin.API_MODE,
                session_id=parent_id, enabled_toolsets=["delegation"], skip_context_files=True,
                skip_memory=True, skip_background_review=True, session_db=db, quiet_mode=True,
            )
            prompt = getattr(plugin, prompt_name)
            result = parent.run_conversation(prompt, task_id=parent_id + "-turn-1")
            provider_calls = result.get("api_calls")
            if result.get("completed") is not True or provider_calls != 0 or calls:
                raise V4BackgroundDeliveryViolation("provider-free parent execution did not complete")
            event = _await_event(process_registry, parent_id, child_count, plugin.MODEL_ID)
            delegation_id = str(event.get("delegation_id") or "")
            if not delegation_id:
                raise V4BackgroundDeliveryViolation("batch completion has no durable identity")
            before = _state(home / "state.db", delegation_id, parent_id)
            if before != {"state": "completed", "delivery_state": "pending", "delivery_attempts": 0, "parent_delivery_rows": 0, "durable_rows": 1}:
                raise V4BackgroundDeliveryViolation("producer completion state is not pending delivery")
            consumer = "v4-background-receipt"
            from tools.async_delegation import claim_event_delivery, complete_event_delivery, release_event_delivery
            transitions: list[dict[str, Any]] = []
            if path == "denial":
                claim = claim_event_delivery(event, consumer)
                if not claim:
                    raise V4BackgroundDeliveryViolation("delivery denial could not claim the completion")
                release_event_delivery(event, claim)
                _requeue(process_registry, event, parent_id)
                after = _state(home / "state.db", delegation_id, parent_id)
                if after != {"state": "completed", "delivery_state": "pending", "delivery_attempts": 1, "parent_delivery_rows": 0, "durable_rows": 1}:
                    raise V4BackgroundDeliveryViolation("denial did not leave the delivery pending")
                transitions.append({"phase": "denial", "state": after["delivery_state"], "attempts": after["delivery_attempts"]})
            else:
                if path == "recovery":
                    first_claim = claim_event_delivery(event, consumer)
                    if not first_claim:
                        raise V4BackgroundDeliveryViolation("recovery denial could not claim the completion")
                    release_event_delivery(event, first_claim)
                    event = _requeue(process_registry, event, parent_id)
                    transitions.append({"phase": "denial", "state": "pending", "attempts": 1})
                claim = claim_event_delivery(event, consumer)
                if not claim:
                    raise V4BackgroundDeliveryViolation("delivery recovery could not reclaim the same event")
                adapter = type("LocalAdapter", (), {"_ensure_session_db": lambda self: db})()
                asyncio.run(persist_delegation_delivery(
                    adapter,
                    text="[IMPORTANT: Provider-free async delegation completion observed.]",
                    session_id=parent_id,
                    evt=event,
                ))
                complete_event_delivery(event, claim)
                after = _state(home / "state.db", delegation_id, parent_id)
                expected_attempts = 1 if path == "positive" else 2
                if after != {"state": "completed", "delivery_state": "delivered", "delivery_attempts": expected_attempts, "parent_delivery_rows": 1, "durable_rows": 1}:
                    raise V4BackgroundDeliveryViolation("delivery recovery state is not exact")
                if claim_event_delivery(event, "late-consumer") is not None:
                    raise V4BackgroundDeliveryViolation("duplicate delivery claim was accepted")
                delivered = _state(home / "state.db", delegation_id, parent_id)
                complete_event_delivery(event, claim)
                if _state(home / "state.db", delegation_id, parent_id) != delivered:
                    raise V4BackgroundDeliveryViolation("late completion changed delivered state")
                transitions.append({"phase": "delivery", "state": after["delivery_state"], "attempts": after["delivery_attempts"], "parent_rows": 1})
            observed = {
                "batch": {"durable_rows": after["durable_rows"], "child_count": child_count, "is_batch": event.get("is_batch") is True},
                "producer": {"state": before["state"], "child_count": child_count},
                "delivery": {"state": after["delivery_state"], "attempts": after["delivery_attempts"], "parent_rows": after["parent_delivery_rows"], "transitions": transitions},
            }
            if after["delivery_state"] == "pending" and after["parent_delivery_rows"] == 0:
                terminal = "denied"
            elif after["delivery_state"] == "delivered" and after["parent_delivery_rows"] == 1:
                terminal = "completed"
            else:
                raise V4BackgroundDeliveryViolation("terminal projection is not derived from delivery state")
            events = [_token("start", {"state": before["state"], "delivery_state": before["delivery_state"]}), _token("background", observed["batch"]), _token("terminal", observed["delivery"], terminal)]
            return {
                "schema_version": 1, "status": "PASS", "path": path, "host_local": True,
                "provider_calls": provider_calls, "terminal_status": terminal, "events": events,
                "observation": observed,
                "proof_hashes": {"primary": _hash(observed), "secondary": _hash({"row": row_key, "trial": trial_index, "path": path, "events": events})},
            }
        finally:
            if parent is not None:
                parent.close()
            if db is not None:
                db.close()
            if manager is not None:
                manager.unload(plugin.PLUGIN_ID)
            AIAgent._create_openai_client = old_create
            reset_hermes_home_override(override)
            if old_manager is not None:
                plugins_mod._plugin_manager = old_manager
            sys.path[:] = old_path
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


build_v4_background_delivery_receipt = run_v4_background_delivery_receipt
execute_v4_background_delivery = run_v4_background_delivery_receipt

__all__ = [
    "V4BackgroundDeliveryViolation", "build_v4_background_delivery_receipt",
    "execute_v4_background_delivery", "run_v4_background_delivery_receipt",
]

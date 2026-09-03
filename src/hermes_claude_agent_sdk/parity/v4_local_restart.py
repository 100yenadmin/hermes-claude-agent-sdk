"""Sealed, provider-free host restart observations for the one v4 restart row."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from .hashing import canonical_json_bytes, sha256_value
from .v4_gateway import Gateway, GatewayRpcError, OpaqueHandle

_ROW_KEY = "openclaw_active/config-restart-capability-flip"
_TRIALS = frozenset({1, 2, 3})
_PATHS = frozenset({"denial", "recovery"})
_HOST_ROOT = Path(__file__).resolve().parents[3].parent / "hermes-agent-runtime-plugin-api"
_SAFE_ENV = {
    "HERMES_MODEL": "claude-fable-5-1",
    "HERMES_TUI_PROVIDER": "claude-agent-sdk",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "PATH": os.defpath,
}


class V4LocalRestartViolation(ValueError):
    """A sealed local restart request or observation is invalid."""


def _root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise V4LocalRestartViolation("task_root is not a path")
    raw = os.fspath(value)
    candidate = Path(raw)
    if (
        not candidate.is_absolute()
        or ".." in candidate.parts
        or len(raw) > 4096
        or candidate.is_symlink()
        or not candidate.exists()
    ):
        raise V4LocalRestartViolation("task_root is not an absolute local directory")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise V4LocalRestartViolation("task_root is unavailable") from None
    user_home = Path.home().resolve()
    if (
        resolved == Path(resolved.anchor)
        or not resolved.is_dir()
        or resolved.is_symlink()
        or resolved == user_home
        or user_home.is_relative_to(resolved)
    ):
        raise V4LocalRestartViolation("task_root is not an absolute local directory")
    return resolved


def _task_env(root: Path) -> tuple[Path, dict[str, str]]:
    try:
        state = Path(tempfile.mkdtemp(prefix=".v4-restart-", dir=os.fspath(root)))
        home, hermes_home = state / "home", state / "hermes-home"
        home.mkdir()
        hermes_home.mkdir()
        # Keep the normal gateway's best-effort startup probes inside this
        # state directory.  These inert caches prevent update/model warmers
        # from consulting a remote endpoint while preserving the real entry
        # point and normal session RPC handlers.
        (hermes_home / "config.yaml").write_text("model_catalog:\n  enabled: false\n", encoding="utf-8")
        (hermes_home / "models_dev_cache.json").write_text('{"v4-local": {}}', encoding="utf-8")
        (hermes_home / ".update_check").write_text(
            '{"ts":4102444800,"behind":0,"rev":null,"ver":"0.21.0"}',
            encoding="utf-8",
        )
    except (OSError, ValueError):
        raise V4LocalRestartViolation("task-local state could not be created") from None
    env = dict(_SAFE_ENV)
    env.update({"HOME": str(home), "HERMES_HOME": str(hermes_home)})
    return state, env


def _rpc(gateway: Any, method: str, params: Mapping[str, Any], *, projector: Any = None) -> dict[str, Any]:
    try:
        response = gateway.call(method, params, projector=projector)
    except GatewayRpcError:
        raise
    except Exception:
        raise V4LocalRestartViolation("gateway RPC failed") from None
    if not isinstance(response, Mapping):
        raise V4LocalRestartViolation("gateway RPC response is malformed")
    result = dict(response)
    if result.get("ok") is not True or result.get("method") != method:
        raise V4LocalRestartViolation("gateway RPC response is not a PASS")
    kind, size, digest = result.get("result_kind"), result.get("result_bytes"), result.get("result_sha256")
    if (
        not isinstance(kind, str)
        or type(size) is not int
        or not 0 <= size <= 1_048_576
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise V4LocalRestartViolation("gateway RPC metadata is malformed")
    return {"ok": True, "method": method, "result_kind": kind, "result_bytes": size, "result_sha256": digest}


def _rpc_error(method: str, error: GatewayRpcError) -> dict[str, Any]:
    return {
        "ok": False,
        "method": method,
        "code": error.code,
        "message_bytes": error.message_bytes,
        "message_sha256": error.message_sha256,
    }


def _close(gateway: Any) -> dict[str, Any]:
    started = bool(getattr(gateway, "started", False))
    try:
        gateway.close()
    except Exception:
        raise V4LocalRestartViolation("gateway close failed") from None
    process = getattr(gateway, "_process", None)
    exited = process is None
    if process is not None:
        try:
            exited = process.poll() is not None
        except Exception:
            exited = False
    return {"operation": "gateway.close", "started": started, "exited": exited}


def _start(gateway: Any) -> dict[str, Any]:
    try:
        gateway.start()
    except Exception:
        raise V4LocalRestartViolation("gateway start failed") from None
    started = getattr(gateway, "started", True)
    if type(started) is not bool or not started:
        raise V4LocalRestartViolation("gateway did not become ready")
    return {"operation": "gateway.start", "ready": True}


def _event(kind: str, value: Mapping[str, Any], terminal: str | None) -> dict[str, Any]:
    encoded = canonical_json_bytes({"kind": kind, "observation": value})
    return {
        "kind": kind,
        "byte_length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "terminal_status": terminal,
    }


def _capture(identities: dict[str, str]):
    def projector(value: object) -> None:
        if isinstance(value, Mapping):
            for name in ("session_id", "stored_session_id"):
                candidate = value.get(name)
                if isinstance(candidate, str) and candidate:
                    identities[name] = candidate
        return None

    return projector


def _gateway(env: Mapping[str, str]) -> Gateway:
    if not _HOST_ROOT.is_dir() or _HOST_ROOT.is_symlink():
        raise V4LocalRestartViolation("normal gateway host is unavailable")
    try:
        return Gateway(python=sys.executable, cwd=_HOST_ROOT, env=env)
    except Exception:
        raise V4LocalRestartViolation("normal gateway could not be constructed") from None


def _run(row_key: str, trial_index: int, path: str, task_root: str | Path) -> dict[str, Any]:
    root = _root(task_root)
    state, env = _task_env(root)
    first = successor = None
    identities: dict[str, str] = {}
    try:
        first = _gateway(env)
        first_start = _start(first)
        created = _rpc(
            first,
            "session.create",
            {"cwd": str(state), "cols": 80},
            projector=_capture(identities),
        )
        live = identities.get("session_id")
        stored = identities.get("stored_session_id")
        if not isinstance(live, str) or not isinstance(stored, str) or not live or not stored:
            raise V4LocalRestartViolation("session identities were not returned")
        live_handle = OpaqueHandle.from_value("live_session", live)
        stored_handle = OpaqueHandle.from_value("stored_session", stored)
        titled = _rpc(first, "session.title", {"session_id": live, "title": "v4-local-restart"})
        closed_rpc = _rpc(first, "session.close", {"session_id": live})
        first_close = _close(first)
        first = None

        successor = _gateway(env)
        successor_start = _start(successor)
        stale = f"v4-stale-{stored}"
        try:
            _rpc(
                successor,
                "session.resume",
                {"session_id": stale, "cols": 80},
            )
        except GatewayRpcError as error:
            stale_resume = _rpc_error("session.resume", error)
        else:
            raise V4LocalRestartViolation("wrong session handle was accepted")
        exact_resume: dict[str, Any] | None = None
        if path == "recovery":
            exact_resume = _rpc(
                successor,
                "session.resume",
                {"session_id": stored, "cols": 80},
                projector=_capture(identities),
            )
            if identities.get("stored_session_id") != stored:
                raise V4LocalRestartViolation("resumed handle identity changed")
        start_value = {"gateway_start": first_start, "create": created, "title": titled, "close_rpc": closed_rpc}
        restart_value = {"first_close": first_close, "successor_start": successor_start}
        terminal_value = {"stale_resume": stale_resume, "exact_resume": exact_resume}
        handles = {"live": live_handle.to_dict(), "stored": stored_handle.to_dict()}
        methods = ["session.create", "session.title", "session.close", "session.resume"]
        if path == "recovery":
            methods.append("session.resume")
        observation = {
            "state": {"root_hash": sha256_value(str(state)), "handles": handles},
            "operations": {"start": start_value, "restart": restart_value, "terminal": terminal_value},
            "rpc_methods": methods,
            "provider_calls": 0,
        }
        terminal_status = "denied" if path == "denial" else "completed"
        events = [
            _event("start", start_value, None),
            _event("restart", restart_value, None),
            _event("terminal", terminal_value, terminal_status),
        ]
        proofs_input = {"handles": handles, "operations": observation["operations"], "terminal": terminal_status}
        return {
            "schema_version": 1,
            "status": "PASS",
            "path": path,
            "host_local": True,
            "provider_calls": 0,
            "terminal_status": terminal_status,
            "events": events,
            "observation": observation,
            "proof_hashes": {
                "primary": sha256_value(proofs_input),
                "secondary": sha256_value({"events": events, "methods": methods}),
            },
        }
    finally:
        for gateway in (first, successor):
            if gateway is not None:
                try:
                    _close(gateway)
                except V4LocalRestartViolation:
                    pass


def run_v4_local_restart(row_key: str, trial_index: int, path: str, task_root: str | Path) -> dict[str, Any]:
    """Run one sealed denial/recovery trial against the normal local Gateway."""
    if (
        not isinstance(row_key, str)
        or not isinstance(path, str)
        or row_key != _ROW_KEY
        or type(trial_index) is not int
        or trial_index not in _TRIALS
        or path not in _PATHS
    ):
        raise V4LocalRestartViolation("row, trial, or path is not admitted")
    return _run(row_key, trial_index, path, task_root)


execute_v4_local_restart = run_v4_local_restart


class V4LocalRestartExecutor:
    """Stateless facade retaining the same sealed four-field call."""

    @staticmethod
    def execute(row_key: str, trial_index: int, path: str, task_root: str | Path) -> dict[str, Any]:
        return run_v4_local_restart(row_key, trial_index, path, task_root)

    run = execute


__all__ = [
    "V4LocalRestartExecutor",
    "V4LocalRestartViolation",
    "execute_v4_local_restart",
    "run_v4_local_restart",
]

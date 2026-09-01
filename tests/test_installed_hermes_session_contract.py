"""Installed Hermes component contract for session handoff.

This test drives the same stdio JSON-RPC gateway used by the Hermes TUI.  It
does not submit a prompt, construct the Claude runtime, or make a model call.
Its purpose is narrower: prove that the installed plugin
can be discovered alongside the exact host contract and that a real Hermes
session can be created, materialized in ``HERMES_HOME/state.db``, found by the
desktop's exact-title lookup, and resumed by a fresh gateway process.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


_TITLE = "Agent SDK component handoff"


def _require(condition: bool, message: str) -> None:
    """Raise without rendering opaque session identifiers into test logs."""

    if not condition:
        raise AssertionError(message)


def _safe_environment(*, home: Path, hermes_home: Path, bin_dir: Path) -> dict[str, str]:
    """Build a credential-free environment rooted entirely under ``tmp_path``."""

    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "LANG",
            "LC_ALL",
            "PATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "WINDIR",
        }
    }
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(hermes_home),
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", os.defpath),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            # Any accidental HTTP client must fail against a local closed port,
            # never reach a provider.  The tested path needs no network.
            "ALL_PROXY": "socks5://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    return env


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"installed command failed rc={result.returncode}: "
            f"{Path(command[0]).name} {command[1:]}\n"
            f"stdout={result.stdout[-2000:]}\n"
            f"stderr={result.stderr[-2000:]}"
        )
    return result


class _Gateway:
    """One installed ``tui_gateway.entry`` process spoken to like the TUI."""

    def __init__(
        self,
        *,
        python: Path,
        cwd: Path,
        env: dict[str, str],
        stderr_path: Path,
    ) -> None:
        self._next_id = 1
        self._stderr_path = stderr_path
        self._stderr = stderr_path.open("w", encoding="utf-8")
        self._messages: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._process = subprocess.Popen(
            [str(python), "-u", "-m", "tui_gateway.entry"],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        self._wait_ready()

    def _pump_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                self._messages.put(payload)
        self._messages.put(None)

    def _read(self, timeout: float = 30.0) -> dict[str, object]:
        try:
            payload = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise AssertionError("installed gateway response timeout") from exc
        if payload is None:
            raise AssertionError(
                f"installed gateway exited before its response; see {self._stderr_path.name}"
            )
        return payload

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            payload = self._read(timeout=max(0.1, deadline - time.monotonic()))
            if payload.get("method") != "event":
                continue
            params = payload.get("params")
            if isinstance(params, dict) and params.get("type") == "gateway.ready":
                return
        raise AssertionError("installed gateway did not announce gateway.ready")

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = str(self._next_id)
        self._next_id += 1
        assert self._process.stdin is not None
        self._process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            + "\n"
        )
        self._process.stdin.flush()
        while True:
            payload = self._read()
            if payload.get("id") != request_id:
                continue
            error = payload.get("error")
            if isinstance(error, dict):
                raise AssertionError(
                    f"installed gateway {method} failed "
                    f"code={error.get('code')} message={error.get('message')}"
                )
            result = payload.get("result")
            _require(isinstance(result, dict), f"installed gateway {method} returned no result")
            return result

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired as exc:
            self._process.terminate()
            self._process.wait(timeout=10)
            raise AssertionError("installed gateway did not stop after stdin EOF") from exc
        finally:
            self._stderr.close()
        _require(self._process.returncode == 0, "installed gateway exited unsuccessfully")


def _console_script(bin_dir: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    script = bin_dir / f"{name}{suffix}"
    _require(script.is_file(), f"installed console script missing: {name}")
    return script


def test_installed_plugin_session_create_persist_list_and_resume(tmp_path: Path) -> None:
    """Prove the zero-model installed component handoff at the public entrypoints."""

    # Keep the venv path itself. Resolving its Python symlink would jump to the
    # base interpreter and hide the venv-owned console scripts beside it.
    python = Path(sys.executable)
    bin_dir = python.parent
    hermes = _console_script(bin_dir, "hermes")
    doctor_cli = _console_script(bin_dir, "hermes-claude-agent-sdk")

    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes-home"
    workspace = tmp_path / "workspace"
    for path in (home, hermes_home, workspace):
        path.mkdir()
    env = _safe_environment(home=home, hermes_home=hermes_home, bin_dir=bin_dir)

    _run(
        [str(hermes), "plugins", "enable", "claude-agent-sdk", "--no-allow-tool-override"],
        cwd=workspace,
        env=env,
    )
    listed = json.loads(
        _run(
            [str(hermes), "plugins", "list", "--json", "--no-bundled"],
            cwd=workspace,
            env=env,
        ).stdout
    )
    plugin_rows = [row for row in listed if row.get("name") == "claude-agent-sdk"]
    _require(len(plugin_rows) == 1, "installed plugin was not listed exactly once")
    _require(plugin_rows[0].get("status") == "enabled", "installed plugin was not enabled")
    _require(plugin_rows[0].get("source") == "entrypoint", "plugin source was not entrypoint")
    _require(plugin_rows[0].get("version") == "0.1.0rc1", "plugin version mismatch")

    doctor = json.loads(
        _run([str(doctor_cli), "doctor", "--json"], cwd=workspace, env=env).stdout
    )
    _require(doctor.get("status") == "compatible", "offline host doctor was not compatible")
    _require(doctor.get("compatible") is True, "offline host compatibility was false")

    registration_probe = r'''
import sys

class RejectSDKImport:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "claude_agent_sdk" or fullname.startswith("claude_agent_sdk."):
            raise AssertionError("Claude SDK imported during plugin registration")
        return None

sys.meta_path.insert(0, RejectSDKImport())
from hermes_cli.plugins import discover_plugins, get_plugin_manager
discover_plugins(force=True)
registration = get_plugin_manager().get_agent_runtime("hermes-claude-agent-sdk")
assert registration is not None
assert registration.descriptor.runtime_id == "hermes-claude-agent-sdk"
assert "claude_agent_sdk" not in sys.modules
print("registration:PASS")
'''
    registration = _run(
        [str(python), "-I", "-c", registration_probe], cwd=workspace, env=env
    )
    _require(
        registration.stdout.strip() == "registration:PASS",
        "plugin registration probe did not pass",
    )

    first = _Gateway(
        python=python,
        cwd=workspace,
        env=env,
        stderr_path=tmp_path / "gateway-first.stderr.log",
    )
    try:
        created = first.call(
            "session.create",
            {
                "cols": 80,
                "cwd": str(workspace),
                "hidden": True,
                "source": "desktop",
                "title": _TITLE,
            },
        )
        runtime_id = created.get("session_id")
        _require(isinstance(runtime_id, str) and bool(runtime_id), "runtime session id missing")
        titled = first.call(
            "session.title", {"session_id": runtime_id, "title": _TITLE}
        )
        _require(titled.get("pending") is False, "session title did not materialize its row")
        visible = first.call(
            "session.list", {"title": _TITLE, "include_hidden": True}
        )
        sessions = visible.get("sessions")
        _require(isinstance(sessions, list) and len(sessions) == 1, "session was not visible")
        stored_id = sessions[0].get("id")
        _require(isinstance(stored_id, str) and bool(stored_id), "stored session id missing")
        _require(
            stored_id == created.get("stored_session_id"),
            "created and listed stored session identities differ",
        )
    finally:
        first.close()

    _require((hermes_home / "state.db").is_file(), "isolated Hermes state.db was not created")

    second = _Gateway(
        python=python,
        cwd=workspace,
        env=env,
        stderr_path=tmp_path / "gateway-second.stderr.log",
    )
    try:
        visible = second.call(
            "session.list", {"title": _TITLE, "include_hidden": True}
        )
        sessions = visible.get("sessions")
        _require(
            isinstance(sessions, list) and len(sessions) == 1,
            "persisted session was not visible after gateway restart",
        )
        _require(
            sessions[0].get("id") == stored_id,
            "persisted session identity changed after gateway restart",
        )
        resumed = second.call(
            "session.resume",
            {
                "session_id": stored_id,
                "lazy": True,
                "omit_messages": True,
                "source": "desktop",
            },
        )
        _require(resumed.get("resumed") == stored_id, "session did not resume by stored identity")
        _require(resumed.get("message_count") == 0, "zero-model session unexpectedly has messages")
        info = resumed.get("info")
        _require(isinstance(info, dict) and info.get("lazy") is True, "resume was not lazy")
    finally:
        second.close()

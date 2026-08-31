from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.native_sandbox import (
    NativeSandboxHost,
    SKILLS_INVENTORY,
    simulate_openclaw_argv,
    tool_schemas,
    write_cli_shim,
)


def test_tool_schemas_are_bounded_and_unknown_tools_fail_closed() -> None:
    schemas = tool_schemas(("read", "write", "exec", "cron"))
    assert [item["function"]["name"] for item in schemas] == [
        "read",
        "write",
        "exec",
        "cron",
    ]
    with pytest.raises(ValueError, match="unsupported tool"):
        tool_schemas(("browser",))


def test_sandbox_denies_once_recovers_and_confines_files(tmp_path: Path) -> None:
    seed = tmp_path / "request.json"
    seed.write_text('{"request":"synthetic"}', encoding="utf-8")
    host = NativeSandboxHost(tmp_path, (seed,))

    async def scenario() -> None:
        denied = await host.execute_tool("read", {"path": "request.json"})
        recovered = await host.execute_tool("read", {"path": "request.json"})
        protected = await host.execute_tool(
            "write", {"path": "request.json", "content": "{}"}
        )
        written = await host.execute_tool(
            "write", {"path": "result.json", "content": '{"ok":true}'}
        )
        escaped = await host.execute_tool(
            "write", {"path": "../escape.json", "content": "{}"}
        )

        assert denied == {
            "error": "synthetic transient denial; retry the same safe operation once"
        }
        assert recovered == '{"request":"synthetic"}'
        assert protected == {"error": "sandbox operation rejected"}
        assert written["status"] == "written"
        assert escaped == {"error": "sandbox operation rejected"}

    asyncio.run(scenario())
    assert host.denial_observed is True
    assert host.recovery_observed is True
    assert seed.read_text(encoding="utf-8") == '{"request":"synthetic"}'
    assert (tmp_path / "result.json").read_text(encoding="utf-8") == '{"ok":true}'
    assert not (tmp_path.parent / "escape.json").exists()


def test_simulated_cli_and_temp_shim_share_the_same_skills_truth() -> None:
    code, output = simulate_openclaw_argv(("openclaw", "skills", "list", "--json"))
    assert code == 0
    assert json.loads(output) == SKILLS_INVENTORY

    with tempfile.TemporaryDirectory() as temp_name:
        shim = Path(temp_name) / "openclaw"
        write_cli_shim(shim)
        completed = subprocess.run(
            (str(shim), "skills", "list", "--json"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == SKILLS_INVENTORY


def test_sandbox_exec_never_accepts_shell_syntax(tmp_path: Path) -> None:
    host = NativeSandboxHost(tmp_path, ())

    async def scenario() -> None:
        await host.execute_tool("exec", {"command": "pwd"})
        result = await host.execute_tool("exec", {"command": "pwd; whoami"})
        assert result == {"error": "sandbox operation rejected"}

    asyncio.run(scenario())

"""Offline proof for the zero-native SDK configuration boundary."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.configuration import SDKSessionConfiguration


@pytest.fixture(autouse=True)
def _unload_public_sdk_after_test():
    """Keep import-laziness assertions in the rest of the suite isolated."""
    yield
    for name in tuple(sys.modules):
        if name == "claude_agent_sdk" or name.startswith("claude_agent_sdk."):
            sys.modules.pop(name, None)


def _configuration() -> SDKSessionConfiguration:
    return SDKSessionConfiguration.create(
        cwd="/synthetic/workspace",
        model="claude-fable-synthetic",
        prompt_snapshot="Hermes-owned prompt snapshot",
        mcp_servers={
            "hermes-tools": {
                "type": "sdk",
                "name": "hermes-tools",
                "version": "1.0.0",
                "instance": object(),
            }
        },
        allowed_tools=("mcp__hermes-tools__pwd",),
    )


def test_option_fields_disable_native_tools_and_use_hermes_mcp_allowlist() -> None:
    from claude_agent_sdk import ClaudeAgentOptions, __version__ as sdk_version
    from hermes_claude_agent_sdk.compatibility import _sdk_metadata

    sdk_report = _sdk_metadata()
    assert sdk_report["installed_version"] == sdk_version
    assert sdk_report["compatible"] is True
    fields = _configuration().option_fields()

    assert set(fields) == {
        "cwd",
        "model",
        "permission_mode",
        "system_prompt",
        "env",
        "setting_sources",
        "tools",
        "mcp_servers",
        "strict_mcp_config",
        "allowed_tools",
    }
    assert fields["system_prompt"] == "Hermes-owned prompt snapshot"
    assert fields["tools"] == []
    assert fields["allowed_tools"] == ["mcp__hermes-tools__pwd"]
    assert all(
        name.startswith("mcp__hermes-tools__")
        for name in fields["allowed_tools"]
    )
    assert fields["strict_mcp_config"] is True
    assert fields["setting_sources"] == []

    # The mapping is accepted by the exact installed public SDK without a
    # provider call or transport process.
    options = ClaudeAgentOptions(**fields)
    assert options.system_prompt == "Hermes-owned prompt snapshot"
    assert options.tools == []
    assert options.allowed_tools == ["mcp__hermes-tools__pwd"]
    assert options.strict_mcp_config is True
    assert options.setting_sources == []


def test_pinned_public_sdk_serializes_explicit_empty_tools_and_exact_prompt(
    tmp_path: Path,
) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    capture_path = tmp_path / "argv.jsonl"
    cli_path = tmp_path / "offline-claude"
    cli_path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

capture = Path(os.environ[\"HERMES_SDK_TEST_CAPTURE\"])
with capture.open(\"a\", encoding=\"utf-8\") as stream:
    stream.write(json.dumps(sys.argv[1:]) + \"\\n\")

if \"--version\" in sys.argv:
    print(\"2.1.258\", flush=True)
    raise SystemExit(0)

for line in sys.stdin:
    request = json.loads(line)
    if request.get(\"type\") != \"control_request\":
        continue
    response = {
        \"type\": \"control_response\",
        \"response\": {
            \"subtype\": \"success\",
            \"request_id\": request[\"request_id\"],
            \"response\": {},
        },
    }
    print(json.dumps(response), flush=True)
""",
        encoding="utf-8",
    )
    cli_path.chmod(cli_path.stat().st_mode | stat.S_IXUSR)

    fields = _configuration().option_fields()
    fields.update(
        {
            "cwd": str(tmp_path),
            "cli_path": str(cli_path),
            "env": {"HERMES_SDK_TEST_CAPTURE": str(capture_path)},
        }
    )
    options = ClaudeAgentOptions(**fields)

    async def scenario() -> None:
        client = ClaudeSDKClient(options)
        await client.connect()
        await client.disconnect()

    asyncio.run(scenario())

    commands = [json.loads(line) for line in capture_path.read_text().splitlines()]
    command = next(argv for argv in commands if "--output-format" in argv)
    assert command[command.index("--tools") : command.index("--tools") + 2] == [
        "--tools",
        "",
    ]
    assert "default" not in command
    assert "Agent" not in command
    prompt_index = command.index("--system-prompt")
    assert command[prompt_index : prompt_index + 2] == [
        "--system-prompt",
        "Hermes-owned prompt snapshot",
    ]
    assert "--append-system-prompt" not in command
    allowed_tools_index = command.index("--allowedTools")
    assert command[allowed_tools_index : allowed_tools_index + 2] == [
        "--allowedTools",
        "mcp__hermes-tools__pwd",
    ]
    assert "--strict-mcp-config" in command
    assert "--setting-sources=" in command
    assert command[command.index("--setting-sources=")] == "--setting-sources="

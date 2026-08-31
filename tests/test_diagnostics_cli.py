"""Credential-free subprocess coverage for the standalone doctor command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HOST_ROOT = Path("/Users/m1/repos/hermes-agent-runtime-plugin-api")


def _run_doctor(*args: str, host: bool = False) -> subprocess.CompletedProcess[str]:
    pythonpath = [str(SRC)]
    if host:
        pythonpath.append(str(HOST_ROOT))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    return subprocess.run(
        [sys.executable, "-m", "hermes_claude_agent_sdk", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_host_is_nonzero_json_and_does_not_import_sdk() -> None:
    result = _run_doctor("doctor", "--json")

    assert result.returncode != 0
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["status"] == "host_unavailable"
    assert report["compatible"] is False
    assert "claude_agent_sdk" not in report
    assert "/Users/" not in result.stdout
    assert "Traceback" not in result.stdout


def test_exact_host_is_zero_json_with_api_capabilities_and_sdk_metadata() -> None:
    result = _run_doctor("doctor", "--json", host=True)

    assert result.returncode == 0
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["status"] == "compatible"
    assert report["compatible"] is True
    assert report["runtime_api"] == {
        "plugin_max": 1,
        "plugin_min": 1,
        "host": 1,
        "compatible": True,
    }
    assert report["capabilities"]["missing"] == []
    assert report["capabilities"]["compatible"] is True
    assert report["sdk"]["distribution"] == "claude-agent-sdk"
    assert report["sdk"]["required_version"] == "0.2.144"
    assert "claude_agent_sdk" not in report
    assert "/Users/" not in result.stdout
    assert "Traceback" not in result.stdout


def test_json_is_stable_and_console_entry_point_is_declared() -> None:
    first = _run_doctor("--json", host=True)
    second = _run_doctor("doctor", "--json", host=True)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stdout.endswith("\n")
    assert "hermes-claude-agent-sdk = \"hermes_claude_agent_sdk.__main__:main\"" in (
        ROOT / "pyproject.toml"
    ).read_text()

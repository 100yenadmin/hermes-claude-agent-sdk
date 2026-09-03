from __future__ import annotations
import subprocess
from pathlib import Path
import pytest
from hermes_claude_agent_sdk.parity import v4_preflights
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.v4_receipts import _projection
def _candidate() -> dict[str, str]:
    return {
        "plugin_sha": "a" * 40,
        "host_sha": "b" * 40,
        "wheel_sha256": "c" * 64,
        "profile_sha256": "d" * 64,
        "sdk_distribution": "claude-agent-sdk",
        "sdk_version": "0.2.151",
        "cli_version": "2.1.258",
        "model": "claude-fable-5-1",
        "runner_id": "hermes-parity-v4",
        "runner_version": "4.0.0",
    }
def _roots(tmp_path: Path) -> tuple[Path, Path]:
    plugin = tmp_path / "plugin"
    host = tmp_path / "host"
    plugin.mkdir()
    host.mkdir()
    (plugin / ".git").mkdir()
    (host / ".git").mkdir()
    (host / "scripts").mkdir()
    (host / "scripts" / "run_tests.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (host / "scripts" / "run_tests.sh").chmod(0o755)
    for spec in v4_preflights.PREFLIGHT_NODE_MAP.values():
        for node in spec.nodes:
            root = plugin if node.owner == "plugin" else host
            source = root / node.path
            source.parent.mkdir(parents=True, exist_ok=True)
            if not source.exists():
                source.write_text("def test_fixture():\n    assert True\n", encoding="utf-8")
    return plugin, host
def _fake_run(monkeypatch: pytest.MonkeyPatch, *, plugin: Path, host: Path, output: str = "1 passed in 0.01s\n", returncode: int = 0, seen: list[tuple[tuple[str, ...], dict[str, str]]] | None = None, dirty: bool = False) -> None:
    def run(argv, *, cwd, env, **kwargs):
        args = tuple(str(item) for item in argv)
        captured = (args, dict(env))
        if seen is not None:
            seen.append(captured)
        if args[:3] == ("git", "rev-parse", "HEAD"):
            sha = "a" * 40 if Path(cwd).resolve() == plugin.resolve() else "b" * 40
            return subprocess.CompletedProcess(args, 0, stdout=sha + "\n", stderr="")
        if args[:3] == ("git", "status", "--porcelain=v1"):
            dirty_output = " M tests/test_fixture.py\n" if dirty and Path(cwd).resolve() == plugin.resolve() else ""
            return subprocess.CompletedProcess(args, 0, stdout=dirty_output, stderr="")
        return subprocess.CompletedProcess(args, returncode, stdout=output, stderr="")
    monkeypatch.setattr(v4_preflights.subprocess, "run", run)
def test_collects_exactly_eight_sanitized_projections_and_binds_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = _roots(tmp_path)
    seen: list[tuple[tuple[str, ...], dict[str, str]]] = []
    _fake_run(monkeypatch, plugin=plugin, host=host, seen=seen)
    output = tmp_path / "projections"
    result = v4_preflights.collect_preflights(
        _candidate(), plugin_root=plugin, host_root=host, output=output
    )
    assert tuple(result) == v4_preflights.OWNERSHIP_PREFLIGHTS
    assert all(item["status"] == "PASS" for item in result.values())
    assert {item.name for item in output.iterdir()} == {
        f"{name}.json" for name in v4_preflights.OWNERSHIP_PREFLIGHTS
    }
    assert all("provider" not in key.casefold() for _, env in seen for key in env)
    assert all(env["HERMES_PARITY_LIVE"] == "0" for _, env in seen)
    assert all(item["candidate_hash"] == sha256_value(_candidate()) for item in result.values())
    assert all(_projection(item, name, sha256_value(_candidate()))[0] == item for name, item in result.items())
def test_commands_are_closed_and_owner_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = _roots(tmp_path)
    seen: list[tuple[tuple[str, ...], dict[str, str]]] = []
    _fake_run(monkeypatch, plugin=plugin, host=host, seen=seen)
    v4_preflights.collect_preflights(_candidate(), plugin, host)
    commands = [args for args, _ in seen if args[0] not in {"git"}]
    expected = [node for spec in v4_preflights.PREFLIGHT_NODE_MAP.values() for node in spec.nodes]
    assert len(commands) == len(expected)
    for args, node in zip(commands, expected, strict=True):
        if node.owner == "plugin":
            assert args[-1] == node.node_id
            assert args[:3] == (v4_preflights.sys.executable, "-m", "pytest")
        else:
            assert args[:2] == (str(host / "scripts/run_tests.sh"), node.path)
            assert args[-1] == f"({node.test_name})"
@pytest.mark.parametrize("output,returncode", [("1 failed in 0.01s\n", 1), ("", 0), ("1 passed in 0.01s\nprovider_calls=1\n", 0)])
def test_nonpassing_or_provider_subprocess_writes_no_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, returncode: int) -> None:
    plugin, host = _roots(tmp_path)
    _fake_run(monkeypatch, plugin=plugin, host=host, output=output, returncode=returncode)
    output = tmp_path / "projections"
    with pytest.raises(v4_preflights.PreflightCollectorViolation):
        v4_preflights.collect_preflights(_candidate(), plugin_root=plugin, host_root=host, output=output)
    assert not output.exists()
@pytest.mark.parametrize("mutation", ["plugin_sha", "host_sha", "dirty"])
def test_identity_and_clean_root_are_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    plugin, host = _roots(tmp_path)
    candidate = _candidate()
    if mutation == "plugin_sha":
        candidate["plugin_sha"] = "e" * 40
    elif mutation == "host_sha":
        candidate["host_sha"] = "e" * 40
    _fake_run(monkeypatch, plugin=plugin, host=host, dirty=mutation == "dirty")
    with pytest.raises(v4_preflights.PreflightCollectorViolation):
        v4_preflights.collect_preflights(candidate, plugin_root=plugin, host_root=host)
def test_missing_or_extra_closed_node_map_fails_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = _roots(tmp_path)
    called = []
    _fake_run(monkeypatch, plugin=plugin, host=host, seen=called)
    original = v4_preflights.PREFLIGHT_NODE_MAP
    monkeypatch.setattr(v4_preflights, "PREFLIGHT_NODE_MAP", {"unexpected": next(iter(original.values()))})
    with pytest.raises(v4_preflights.PreflightCollectorViolation):
        v4_preflights.collect_preflights(_candidate(), plugin_root=plugin, host_root=host)
    assert called == []
def test_raw_caller_environment_is_not_inherited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = _roots(tmp_path)
    seen: list[tuple[tuple[str, ...], dict[str, str]]] = []
    _fake_run(monkeypatch, plugin=plugin, host=host, seen=seen)
    for key in ("HOME", "HERMES_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY", "EXTRA_USAGE", "HERMES_EXTRA_USAGE", "METERED", "HERMES_PARITY_LIVE"):
        monkeypatch.setenv(key, "caller-secret")
    v4_preflights.collect_preflights(_candidate(), plugin_root=plugin, host_root=host)
    for _, env in seen:
        assert env["HERMES_PARITY_LIVE"] == "0"
        assert env["HOME"] != "caller-secret"
        assert env["HERMES_HOME"] != "caller-secret"
        assert all(key not in env for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY", "EXTRA_USAGE", "HERMES_EXTRA_USAGE", "METERED"))
def test_projection_writer_rejects_caller_supplied_raw_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = _roots(tmp_path)
    _fake_run(monkeypatch, plugin=plugin, host=host)
    projections = v4_preflights.collect_preflights(_candidate(), plugin, host)
    projections["background_owner"]["observation"]["raw_prompt"] = "caller assertion"
    with pytest.raises(v4_preflights.PreflightCollectorViolation):
        v4_preflights.write_preflight_projections(projections, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
def test_projection_documents_have_only_receipt_schema_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = _roots(tmp_path)
    _fake_run(monkeypatch, plugin=plugin, host=host)
    result = v4_preflights.collect_preflights(_candidate(), plugin_root=plugin, host_root=host)
    assert all(set(item) == {"schema_version", "name", "candidate_hash", "status", "source", "observation"} for item in result.values())
    assert all(set(item["source"]) == {"executable", "source_ref", "test_id"} for item in result.values())
    assert all(set(item["observation"]) >= {"exit_status", "passed_count", "node_count", "test_hash", "command_hash", "provider_calls", "live"} for item in result.values())

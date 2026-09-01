from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from hermes_claude_agent_sdk.parity import focused_suite
from hermes_claude_agent_sdk.parity.focused_suite import (
    boundary_execution_ids,
    boundary_focused_suite,
)
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext


def _context(catalog, capability_id: str) -> ExecutionContext:
    return ExecutionContext(
        capability=catalog.by_id[capability_id],
        path="positive",
        trial_index=1,
        profile_id="fable-v3-isolated",
        profile_hash="3" * 64,
        plugin_sha="1" * 40,
        host_sha="2" * 40,
        sdk_version="0.2.144",
        runner_version="3.0.0",
        inventory_hash="4" * 64,
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=180,
        repo_root="/synthetic/repo",
    )


def test_every_boundary_catalog_row_has_an_exact_executor_mapping(catalog) -> None:
    expected = {
        item.execution_id
        for item in catalog.capabilities
        if item.source_pack == "agent_sdk_boundary"
    }
    assert set(boundary_execution_ids()) == expected
    assert set(focused_suite._BOUNDARY_PATH_CONTROLS) == {
        item.capability_id
        for item in catalog.capabilities
        if item.source_pack == "agent_sdk_boundary"
    }
    assert all(
        controls["denial"] and controls["recovery"]
        for controls in focused_suite._BOUNDARY_PATH_CONTROLS.values()
    )
    assert len(expected) == 23


def test_focused_suite_executes_and_proves_each_path_independently(
    catalog,
    monkeypatch,
    tmp_path,
) -> None:
    calls = []
    observed_environments = []

    def successful_preflight(_, __, environment):
        observed_environments.append(environment)
        home = Path(environment["HOME"])
        assert home.is_dir()
        assert Path(environment["HERMES_HOME"]).is_dir()
        assert environment["PYTHONPATH"] == "/synthetic/repo/src"
        return None

    monkeypatch.setattr(focused_suite, "_exact_source_preflight", successful_preflight)

    def successful_run(*args, environment, **kwargs):
        calls.append((args, kwargs))
        observed_environments.append(environment)
        home = Path(environment["HOME"])
        assert home.is_dir()
        assert Path(environment["HERMES_HOME"]).is_dir()
        assert home != tmp_path
        assert environment["PYTHONPATH"] == "/synthetic/repo/src"
        return subprocess.CompletedProcess(
            args=("pytest",),
            returncode=0,
            stdout=b"2 passed",
            stderr=b"",
        )

    monkeypatch.setattr(focused_suite, "_run", successful_run)

    result = asyncio.run(
        boundary_focused_suite(
            _context(
                catalog,
                "boundary:terminal-error-warm-query-reuse",
            )
        )
    )

    assert result.turn_count == 0
    assert len(calls) == 3
    assert result.outcomes["positive"].classification is ExecutionClassification.COMPLETE
    assert (
        result.outcomes["denial"].classification
        is ExecutionClassification.EXPECTED_NEGATIVE
    )
    assert result.outcomes["recovery"].classification is ExecutionClassification.COMPLETE
    assert all(
        outcome.primary_proof_hash and outcome.secondary_proof_hash
        for outcome in result.outcomes.values()
    )
    assert len(
        {outcome.primary_proof_hash for outcome in result.outcomes.values()}
    ) == 3
    for outcome in result.outcomes.values():
        assert [event["kind"] for event in outcome.normalized_events] == list(
            catalog.by_id[
                "boundary:terminal-error-warm-query-reuse"
            ].expected_trace
        )
    assert len(observed_environments) == 4
    assert all(
        environment is observed_environments[0]
        for environment in observed_environments
    )
    assert not Path(observed_environments[0]["HOME"]).exists()
    assert not Path(observed_environments[0]["HERMES_HOME"]).exists()


def test_focused_suite_test_failure_is_a_verified_failure(catalog, monkeypatch) -> None:
    monkeypatch.setattr(focused_suite, "_exact_source_preflight", lambda *_: None)
    monkeypatch.setattr(
        focused_suite,
        "_run",
        lambda *_, **__: subprocess.CompletedProcess(
            args=("pytest",),
            returncode=1,
            stdout=b"failed",
            stderr=b"bounded",
        ),
    )

    result = asyncio.run(
        boundary_focused_suite(
            _context(
                catalog,
                "boundary:missing-terminal-result-fails-closed",
            )
        )
    )

    assert all(
        outcome.classification is ExecutionClassification.VERIFIED_FAILURE
        for outcome in result.outcomes.values()
    )
    assert result.outcomes["positive"].reason_code == "focused_positive_suite_failed"
    assert result.outcomes["denial"].reason_code == "focused_denial_suite_failed"
    assert result.outcomes["recovery"].reason_code == "focused_recovery_suite_failed"
    assert all(outcome.primary_proof_hash is None for outcome in result.outcomes.values())


def test_focused_suite_environment_does_not_forward_secret_shaped_keys(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("HERMES_HOME", "/ambient/hermes")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/ambient/shadow")
    environment = focused_suite._safe_environment()

    assert environment["PATH"] == "/usr/bin"
    assert "ANTHROPIC_API_KEY" not in environment
    assert "HOME" not in environment
    assert "HERMES_HOME" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_source_preflight_forwards_isolated_environment_to_git_subprocesses(
    catalog,
    monkeypatch,
    tmp_path,
) -> None:
    root = tmp_path / "repo"
    (root / "qa").mkdir(parents=True)
    (root / "qa" / "parity-contract-v3.yaml").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(focused_suite, "load_catalog", lambda _: catalog)
    monkeypatch.setenv("HERMES_PARITY_PLUGIN_SHA", "1" * 40)
    monkeypatch.setenv("HERMES_AGENT_HOST_SHA", "2" * 40)

    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    hermes_home.mkdir(parents=True)
    monkeypatch.setenv("PYTHONPATH", "/ambient/shadow")
    environment = focused_suite._safe_environment(home=home, source_root=root)
    assert environment["PYTHONPATH"] == str(root / "src")
    calls = []

    def successful_run(argv, *, cwd, timeout, environment):
        calls.append((argv, cwd, timeout, environment))
        assert Path(environment["HOME"]).is_dir()
        assert Path(environment["HERMES_HOME"]).is_dir()
        stdout = b"1" * 40 if argv[1:3] == ("rev-parse", "HEAD") else b""
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=stdout,
            stderr=b"",
        )

    monkeypatch.setattr(focused_suite, "_run", successful_run)

    assert focused_suite._exact_source_preflight(
        _context(catalog, "boundary:terminal-error-warm-query-reuse"),
        root,
        environment,
    ) is None
    assert [call[0][:3] for call in calls] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain"),
    ]
    assert all(call[3] is environment for call in calls)

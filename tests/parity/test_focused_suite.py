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
    assert len(expected) == 23


def test_focused_suite_does_not_fabricate_unexecuted_path_outcomes(
    catalog,
    monkeypatch,
    tmp_path,
) -> None:
    observed_environments = []

    def successful_preflight(_, __, environment):
        observed_environments.append(environment)
        home = Path(environment["HOME"])
        assert home.is_dir()
        assert Path(environment["HERMES_HOME"]).is_dir()
        return None

    monkeypatch.setattr(focused_suite, "_exact_source_preflight", successful_preflight)

    def successful_run(*_, environment, **__):
        observed_environments.append(environment)
        home = Path(environment["HOME"])
        assert home.is_dir()
        assert Path(environment["HERMES_HOME"]).is_dir()
        assert home != tmp_path
        return subprocess.CompletedProcess(
            args=("pytest",),
            returncode=0,
            stdout=b"2 passed",
            stderr=b"",
        )

    monkeypatch.setattr(
        focused_suite,
        "_run",
        successful_run,
    )

    result = asyncio.run(
        boundary_focused_suite(
            _context(
                catalog,
                "boundary:terminal-error-warm-query-reuse",
            )
        )
    )

    assert result.turn_count == 0
    assert result.outcomes["positive"].classification is ExecutionClassification.COMPLETE
    assert (
        result.outcomes["denial"].classification
        is ExecutionClassification.PENDING
    )
    assert result.outcomes["recovery"].classification is ExecutionClassification.PENDING
    assert result.outcomes["positive"].primary_proof_hash
    assert result.outcomes["positive"].secondary_proof_hash
    assert result.outcomes["denial"].primary_proof_hash is None
    assert result.outcomes["denial"].secondary_proof_hash is None
    assert result.outcomes["recovery"].primary_proof_hash is None
    assert result.outcomes["recovery"].secondary_proof_hash is None
    assert [event["kind"] for event in result.outcomes["positive"].normalized_events] == list(
        catalog.by_id[
            "boundary:terminal-error-warm-query-reuse"
        ].expected_trace
    )
    assert not result.outcomes["denial"].normalized_events
    assert not result.outcomes["recovery"].normalized_events
    assert len(observed_environments) == 2
    assert observed_environments[0] is observed_environments[1]
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

    assert result.outcomes["positive"].classification is ExecutionClassification.VERIFIED_FAILURE
    assert result.outcomes["positive"].reason_code == "focused_suite_failed"
    assert result.outcomes["denial"].classification is ExecutionClassification.PENDING
    assert result.outcomes["denial"].reason_code == "focused_denial_path_not_executed"
    assert result.outcomes["recovery"].classification is ExecutionClassification.PENDING
    assert result.outcomes["recovery"].reason_code == "focused_recovery_path_not_executed"
    assert all(outcome.primary_proof_hash is None for outcome in result.outcomes.values())


def test_focused_suite_environment_does_not_forward_secret_shaped_keys(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("HERMES_HOME", "/ambient/hermes")
    monkeypatch.setenv("PATH", "/usr/bin")
    environment = focused_suite._safe_environment()

    assert environment["PATH"] == "/usr/bin"
    assert "ANTHROPIC_API_KEY" not in environment
    assert "HOME" not in environment
    assert "HERMES_HOME" not in environment
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
    environment = focused_suite._safe_environment(home=home)
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

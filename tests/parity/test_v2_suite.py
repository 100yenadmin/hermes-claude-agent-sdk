from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from hermes_claude_agent_sdk.parity import v2_suite
from hermes_claude_agent_sdk.parity.v2_suite import (
    _V2_NODES,
    _V2_PATH_CONTROLS,
    _executable_path,
    v2_mapped_suite,
    v2_execution_ids,
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


def test_every_v2_non_soak_row_has_one_exact_mapping(catalog) -> None:
    source_ids = {
        item.capability_id
        for item in catalog.capabilities
        if item.source_pack == "v2_non_soak"
    }
    assert len(source_ids) == 53
    assert set(_V2_NODES) == source_ids
    assert len(v2_execution_ids()) == 53
    assert len(set(v2_execution_ids())) == 53
    assert set(_V2_PATH_CONTROLS) == source_ids
    assert all(
        controls["denial"] and controls["recovery"]
        for controls in _V2_PATH_CONTROLS.values()
    )


def test_codex_route_paths_use_capability_specific_controls() -> None:
    controls = _V2_PATH_CONTROLS["v2:auth-02"]

    assert {
        node.node_id for node in controls["denial"]
    } >= {
        "tests/tools/test_delegate_routes.py::test_malformed_unknown_partial_and_credential_failures_are_atomic",
        "tests/tools/test_delegate_routes.py::test_metered_and_unknown_billing_fail_closed",
    }
    assert {
        node.node_id for node in controls["recovery"]
    } >= {
        "tests/tools/test_delegate_routes.py::test_valid_codex_route_receipt_and_precedence",
    }
    assert all(node.repo == "v2" for path in controls.values() for node in path)


def test_v2_mapping_never_names_forbidden_live_surfaces() -> None:
    rendered = repr((_V2_NODES, _V2_PATH_CONTROLS)).lower()
    assert "telegram" not in rendered
    assert "customer" not in rendered
    assert "shared-eva" not in rendered


def test_virtualenv_python_path_is_not_resolved_to_its_base_interpreter(
    tmp_path: Path,
) -> None:
    target = tmp_path / "base-python"
    target.write_text("synthetic", encoding="utf-8")
    virtualenv = tmp_path / "venv" / "bin"
    virtualenv.mkdir(parents=True)
    link = virtualenv / "python"
    link.symlink_to(target)

    assert _executable_path(str(link)) == link
    assert _executable_path(str(link)) != link.resolve()


def test_v2_successful_source_tests_execute_and_prove_each_path(
    catalog,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(v2_suite, "_exact_source_preflight", lambda *_: None)
    monkeypatch.setattr(v2_suite, "_exact_git_checkout", lambda *_: True)
    host_root = tmp_path / "host"
    v2_root = tmp_path / "v2"
    host_root.mkdir()
    v2_root.mkdir()
    host_python = host_root / "bin" / "python"
    host_python.parent.mkdir()
    host_python.write_text("synthetic", encoding="utf-8")
    monkeypatch.setenv("HERMES_AGENT_HOST_ROOT", str(host_root))
    monkeypatch.setenv("HERMES_PARITY_V2_ROOT", str(v2_root))
    monkeypatch.setenv("HERMES_PARITY_HOST_PYTHON", str(host_python))
    calls = []

    def successful_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=("pytest",),
            returncode=0,
            stdout=b"1 passed",
            stderr=b"",
        )

    monkeypatch.setattr(v2_suite, "_run_nodes", successful_run)

    result = asyncio.run(v2_mapped_suite(_context(catalog, "v2:auth-02")))

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


def test_cross_stage_rows_remain_pending_without_the_bound_receipt(
    catalog,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(v2_suite, "_exact_source_preflight", lambda *_: None)
    monkeypatch.setattr(v2_suite, "_exact_git_checkout", lambda *_: True)
    host_root = tmp_path / "host"
    v2_root = tmp_path / "v2"
    host_root.mkdir()
    v2_root.mkdir()
    host_python = host_root / "python"
    host_python.write_text("synthetic", encoding="utf-8")
    monkeypatch.setenv("HERMES_AGENT_HOST_ROOT", str(host_root))
    monkeypatch.setenv("HERMES_PARITY_V2_ROOT", str(v2_root))
    monkeypatch.setenv("HERMES_PARITY_HOST_PYTHON", str(host_python))
    monkeypatch.delenv("HERMES_PARITY_LEAN_RECEIPT", raising=False)

    result = asyncio.run(v2_mapped_suite(_context(catalog, "v2:eff-01")))

    assert all(
        outcome.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
        and outcome.reason_code == "v2_cross_stage_receipt_pending"
        for outcome in result.outcomes.values()
    )

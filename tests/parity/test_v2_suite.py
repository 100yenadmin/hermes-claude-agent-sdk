from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity import v2_suite
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext
from hermes_claude_agent_sdk.parity.v2_suite import (
    _V2_NODES,
    _executable_path,
    v2_execution_ids,
    v2_mapped_suite,
)


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


@pytest.mark.parametrize(
    ("capability_id", "expected_nodes"),
    [
        (
            "v2:parent-03",
            (
                "tests/test_runtime_sdk_integration.py::test_compatible_successive_turns_reuse_one_client_reader_and_resume_state",
            ),
        ),
        (
            "v2:orch-01",
            (
                "tests/test_zero_native_configuration.py::test_pinned_public_sdk_serializes_explicit_empty_tools_and_exact_prompt",
            ),
        ),
        (
            "v2:orch-02",
            (
                "tests/test_sdk_session.py::test_post_terminal_sdk_output_is_a_protocol_failure_without_background_delivery",
            ),
        ),
        (
            "v2:ops-08",
            (
                "tests/test_zero_native_configuration.py::test_option_fields_disable_native_tools_and_use_hermes_mcp_allowlist",
            ),
        ),
        (
            "v2:eff-02",
            (
                "tests/test_runtime_sdk_integration.py::test_compatible_successive_turns_reuse_one_client_reader_and_resume_state",
            ),
        ),
    ],
)
def test_zero_native_successor_mappings_use_current_plugin_tests(
    capability_id: str,
    expected_nodes: tuple[str, ...],
) -> None:
    mapped_nodes = tuple(node.node_id for node in _V2_NODES[capability_id])

    assert mapped_nodes == expected_nodes
    assert all(
        Path(node.split("::", 1)[0]).is_file()
        for node in mapped_nodes
    )


def test_v2_mapping_never_names_forbidden_live_surfaces() -> None:
    rendered = repr(_V2_NODES).lower()
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


def test_v2_successful_source_tests_do_not_fabricate_unexecuted_path_outcomes(
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

    assert len(calls) == 1
    assert result.outcomes["positive"].classification is ExecutionClassification.COMPLETE
    assert result.outcomes["denial"].classification is ExecutionClassification.PENDING
    assert result.outcomes["recovery"].classification is ExecutionClassification.PENDING
    assert result.outcomes["positive"].primary_proof_hash
    assert result.outcomes["positive"].secondary_proof_hash
    assert result.outcomes["denial"].primary_proof_hash is None
    assert result.outcomes["recovery"].primary_proof_hash is None

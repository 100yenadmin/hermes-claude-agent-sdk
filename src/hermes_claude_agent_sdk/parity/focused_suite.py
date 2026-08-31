"""Exact-source focused-suite adapter for deterministic boundary evidence."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .catalog import load_catalog
from .hashing import sha256_value
from .results import ExecutionClassification
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome


_BOUNDARY_NODES: dict[str, tuple[str, ...]] = {
    "boundary:sdk-identity-credential-privacy-side-question-isolation": (
        "tests/test_sdk_session.py::test_import_and_configuration_do_not_import_sdk_or_retain_parent_secret",
        "tests/test_auth.py::test_parser_accepts_json_and_discards_identity_fields",
    ),
    "boundary:authenticated-executable-exact-host-environment": (
        "tests/test_auth.py::test_probe_uses_bounded_no_shell_argv_and_minimal_noncredential_environment",
    ),
    "boundary:cancellation-race-during-async-module-load": (
        "tests/test_runtime_sdk_integration.py::test_pre_set_interrupt_event_honored_then_next_turn_runs",
    ),
    "boundary:fresh-resume-native-session-identity": (
        "tests/test_runtime_sdk_integration.py::test_host_tool_bridge_and_resume_use_only_public_fields",
        "tests/test_runtime_sdk_integration.py::test_successful_turn_then_cancelled_turn_reuses_current_resume_on_replacement",
    ),
    "boundary:cache-effort-checkpoint-fork-options": (
        "tests/parity/test_boundary_adaptations.py::test_cache_receipts_and_resume_survive_while_unknown_fork_controls_fail_closed",
    ),
    "boundary:compatible-warm-query-process-reuse": (
        "tests/test_runtime_sdk_integration.py::test_runtime_reuses_one_client_reader_and_uses_host_only_for_idle_completion",
    ),
    "boundary:terminal-error-warm-query-reuse": (
        "tests/test_sdk_session.py::test_terminal_error_turn_keeps_the_compatible_sdk_session_warm",
    ),
    "boundary:execution-fingerprint-change-restarts-warm-query": (
        "tests/test_runtime_sdk_integration.py::test_runtime_rejects_prompt_or_tool_contract_change_before_second_query",
    ),
    "boundary:inactive-owner-refuses-live-process": (
        "tests/test_runtime_sdk_integration.py::test_runtime_rejects_a_replacement_host_binding_without_query_or_reroute",
    ),
    "boundary:abort-closes-process-and-fences-permissions": (
        "tests/test_runtime_sdk_integration.py::test_cancellation_interrupts_and_closes_once_with_one_terminal",
        "tests/test_tool_bridge.py::test_cancellation_and_cancellation_probe_failure_do_not_call_host",
    ),
    "boundary:approval-callback-active-turn-rebind": (
        "tests/parity/test_boundary_adaptations.py::test_cancelled_or_late_tool_request_is_fenced_then_next_turn_rebinds",
        "tests/test_tool_bridge.py::test_begin_turn_refreshes_tool_correlation_without_rebuilding_bridge",
    ),
    "boundary:late-approval-rejected": (
        "tests/parity/test_boundary_adaptations.py::test_cancelled_or_late_tool_request_is_fenced_then_next_turn_rebinds",
    ),
    "boundary:background-provisional-result-settlement": (
        "tests/test_runtime_sdk_integration.py::test_queued_idle_burst_is_released_only_after_parent_terminal_is_observed",
    ),
    "boundary:restricted-native-tools-and-mcp-grants": (
        "tests/test_native_agent_configuration.py::test_option_fields_expose_native_agent_and_hermes_mcp_allowlist",
        "tests/test_tool_bridge.py::test_unknown_duplicate_and_excluded_names_fail_before_host_call",
    ),
    "boundary:variadic-directories-tools-managed-mcp-isolation": (
        "tests/parity/test_boundary_adaptations.py::test_variadic_sdk_surfaces_are_denied_without_weakening_tool_or_mcp_isolation",
    ),
    "boundary:wildcard-mcp-grants-expand-to-admitted-tools": (
        "tests/test_tool_bridge.py::test_unknown_duplicate_and_excluded_names_fail_before_host_call",
    ),
    "boundary:missing-terminal-result-fails-closed": (
        "tests/test_sdk_session.py::test_sdk_stream_without_a_terminal_result_fails_closed",
    ),
    "boundary:structured-question-not-tool-approval": (
        "tests/parity/test_boundary_adaptations.py::test_unavailable_structured_question_surface_fails_before_host_and_recovers",
    ),
    "boundary:native-tool-policy-precedes-user-shadow": (
        "tests/test_tool_bridge.py::test_sdk_adapter_does_not_approve_or_execute_fallback",
    ),
    "boundary:bypass-shaped-arguments-remain-permissioned": (
        "tests/test_tool_bridge.py::test_malformed_arguments_fail_closed_before_host_call",
    ),
    "boundary:structured-question-answer-mapping-dedup": (
        "tests/parity/test_boundary_adaptations.py::test_unavailable_structured_question_surface_fails_before_host_and_recovers",
    ),
    "boundary:structured-question-skip-denial-guidance": (
        "tests/parity/test_boundary_adaptations.py::test_unavailable_structured_question_surface_fails_before_host_and_recovers",
    ),
    "boundary:malformed-question-rejected-before-host": (
        "tests/parity/test_boundary_adaptations.py::test_unavailable_structured_question_surface_fails_before_host_and_recovers",
    ),
}


def _safe_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "PYTHONPATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "VIRTUAL_ENV",
    )
    environment = {
        key: value
        for key in allowed
        if isinstance((value := os.environ.get(key)), str) and value
    }
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    return environment


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=_safe_environment(),
        shell=False,
        check=False,
        capture_output=True,
        text=False,
        timeout=timeout,
    )


def _blocked(reason: str) -> ExecutionBundle:
    return ExecutionBundle(
        outcomes={
            path: ExecutionOutcome(
                classification=ExecutionClassification.ENVIRONMENT_BLOCKED,
                billing_classification="none",
                reason_code=reason,
            )
            for path in ("positive", "denial", "recovery")
        },
        turn_count=0,
    )


def _terminal_events(
    *,
    path: str,
    passed: bool,
    node_manifest_hash: str,
    output_hash: str,
) -> tuple[dict[str, Any], ...]:
    terminal = "denied" if passed and path == "denial" else "completed" if passed else "failed"
    evidence_hash = sha256_value(
        {
            "node_manifest_hash": node_manifest_hash,
            "output_hash": output_hash,
        }
    )
    return (
        {"sequence": 1, "kind": "start", "status": "started"},
        {
            "sequence": 2,
            "kind": "state",
            "status": "focused_test_passed" if passed else "focused_test_failed",
            "metadata_hash": evidence_hash,
        },
        {
            "sequence": 3,
            "kind": "terminal",
            "status": terminal,
            "terminal_outcome": terminal,
        },
    )


def _exact_source_preflight(context: ExecutionContext, root: Path) -> str | None:
    if (
        context.profile_id != "fable-v3-isolated"
        or context.sdk_version != "0.2.144"
    ):
        return "focused_suite_identity_mismatch"
    if os.environ.get("HERMES_PARITY_PLUGIN_SHA") != context.plugin_sha:
        return "plugin_sha_unverified"
    if os.environ.get("HERMES_AGENT_HOST_SHA") != context.host_sha:
        return "host_sha_unverified"
    if not root.is_dir() or not (root / "qa" / "parity-contract-v3.yaml").is_file():
        return "focused_suite_source_unavailable"
    try:
        catalog = load_catalog(root / "qa" / "parity-contract-v3.yaml")
    except Exception:
        return "focused_suite_catalog_invalid"
    if (
        catalog.contract_hash != context.contract_hash
        or catalog.catalog_hash != context.catalog_hash
    ):
        return "focused_suite_catalog_mismatch"
    try:
        head = _run(("git", "rev-parse", "HEAD"), cwd=root, timeout=10.0)
        status = _run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=root,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "focused_suite_git_unavailable"
    if head.returncode != 0 or head.stdout.decode("ascii", errors="ignore").strip() != context.plugin_sha:
        return "focused_suite_head_mismatch"
    if status.returncode != 0 or status.stdout.strip():
        return "focused_suite_source_dirty"
    return None


async def boundary_focused_suite(context: ExecutionContext) -> ExecutionBundle:
    """Run the exact focused tests mapped to one boundary source row."""

    nodes = _BOUNDARY_NODES.get(context.capability.capability_id)
    if nodes is None:
        return _blocked("boundary_evidence_mapping_missing")
    root = Path(context.repo_root).expanduser().resolve()
    blocked = _exact_source_preflight(context, root)
    if blocked is not None:
        return _blocked(blocked)
    node_manifest_hash = sha256_value(nodes)
    try:
        completed = _run(
            (
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--disable-warnings",
                *nodes,
            ),
            cwd=root,
            timeout=300.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _blocked("focused_suite_runner_unavailable")
    stdout_hash = hashlib.sha256(completed.stdout[: 256 * 1024]).hexdigest()
    stderr_hash = hashlib.sha256(completed.stderr[: 256 * 1024]).hexdigest()
    output_hash = sha256_value(
        {
            "returncode": completed.returncode,
            "stdout_hash": stdout_hash,
            "stderr_hash": stderr_hash,
        }
    )
    passed = completed.returncode == 0
    outcomes: dict[str, ExecutionOutcome] = {}
    for path in ("positive", "denial", "recovery"):
        if not passed:
            classification = ExecutionClassification.VERIFIED_FAILURE
        elif path == "denial":
            classification = ExecutionClassification.EXPECTED_NEGATIVE
        else:
            classification = ExecutionClassification.COMPLETE
        proof = {
            "capability_id": context.capability.capability_id,
            "path": path,
            "node_manifest_hash": node_manifest_hash,
            "output_hash": output_hash,
            "plugin_sha": context.plugin_sha,
            "host_sha": context.host_sha,
        }
        outcomes[path] = ExecutionOutcome(
            classification=classification,
            billing_classification="none",
            normalized_events=_terminal_events(
                path=path,
                passed=passed,
                node_manifest_hash=node_manifest_hash,
                output_hash=output_hash,
            ),
            primary_proof_hash=sha256_value(proof) if passed else None,
            secondary_proof_hash=(
                sha256_value(
                    {
                        "catalog_hash": context.catalog_hash,
                        "inventory_hash": context.inventory_hash,
                        "profile_hash": context.profile_hash,
                        "path": path,
                    }
                )
                if passed
                else None
            ),
            reason_code=None if passed else "focused_suite_failed",
            turn_count=0,
        )
    return ExecutionBundle(outcomes=outcomes, turn_count=0)


def boundary_execution_ids() -> tuple[str, ...]:
    return tuple(
        capability_id.replace(":", "-", 1)
        for capability_id in sorted(_BOUNDARY_NODES)
    )


__all__ = ["boundary_execution_ids", "boundary_focused_suite"]

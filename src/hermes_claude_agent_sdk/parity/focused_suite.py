"""Exact-source focused-suite adapter for deterministic boundary evidence."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from .catalog import load_catalog
from .hashing import sha256_value
from .results import ExecutionClassification
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome
from .sdk_identity import candidate_sdk_failure
from .trace import normalized_path_events

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


def _safe_environment(
    *,
    home: Path | None = None,
    source_root: Path | None = None,
) -> dict[str, str]:
    allowed = (
        "PATH",
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
    if home is not None:
        environment.update(
            {
                "HOME": str(home),
                "HERMES_HOME": str(home / ".hermes"),
            }
        )
    if source_root is not None:
        environment["PYTHONPATH"] = str(source_root / "src")
    return environment


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(environment) if environment is not None else _safe_environment(),
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


def _pending_path(reason: str) -> ExecutionOutcome:
    """Leave a path unqualified until it has its own executable evidence."""

    return ExecutionOutcome(
        classification=ExecutionClassification.PENDING,
        billing_classification="none",
        reason_code=reason,
    )


def _exact_source_preflight(
    context: ExecutionContext,
    root: Path,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    if context.profile_id != "fable-v3-isolated":
        return "focused_suite_identity_mismatch"
    sdk_failure = candidate_sdk_failure(context.sdk_version)
    if sdk_failure is not None:
        return sdk_failure
    if os.environ.get("HERMES_PARITY_PLUGIN_SHA") != context.plugin_sha:
        return "plugin_sha_unverified"
    if os.environ.get("HERMES_AGENT_HOST_SHA") != context.host_sha:
        return "host_sha_unverified"
    if not root.is_dir() or not (root / "qa" / "parity-contract-v3.yaml").is_file():
        return "focused_suite_source_unavailable"
    try:
        catalog = load_catalog(root / "qa" / "parity-contract-v3.yaml")
    except Exception:  # noqa: BLE001 - catalog faults fail the gate closed
        return "focused_suite_catalog_invalid"
    if (
        catalog.contract_hash != context.contract_hash
        or catalog.catalog_hash != context.catalog_hash
    ):
        return "focused_suite_catalog_mismatch"
    try:
        head = _run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            timeout=10.0,
            environment=environment,
        )
        status = _run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=root,
            timeout=10.0,
            environment=environment,
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
    node_manifest_hash = sha256_value(nodes)
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-parity-v3-focused-") as home_name:
            home = Path(home_name)
            hermes_home = home / ".hermes"
            hermes_home.mkdir()
            environment = _safe_environment(home=home, source_root=root)
            blocked = _exact_source_preflight(context, root, environment)
            if blocked is not None:
                return _blocked(blocked)
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
                environment=environment,
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
    positive_proof = {
        "capability_id": context.capability.capability_id,
        "path": "positive",
        "node_manifest_hash": node_manifest_hash,
        "output_hash": output_hash,
        "plugin_sha": context.plugin_sha,
        "host_sha": context.host_sha,
    }
    if passed:
        positive = ExecutionOutcome(
            classification=ExecutionClassification.COMPLETE,
            billing_classification="none",
            normalized_events=normalized_path_events(
                context.capability.expected_trace,
                path="positive",
                evidence_hash=sha256_value(
                    {
                        "node_manifest_hash": node_manifest_hash,
                        "output_hash": output_hash,
                    }
                ),
            ),
            primary_proof_hash=sha256_value(positive_proof),
            secondary_proof_hash=sha256_value(
                {
                    "catalog_hash": context.catalog_hash,
                    "inventory_hash": context.inventory_hash,
                    "profile_hash": context.profile_hash,
                    "path": "positive",
                }
            ),
            turn_count=0,
        )
    else:
        positive = ExecutionOutcome(
            classification=ExecutionClassification.VERIFIED_FAILURE,
            billing_classification="none",
            normalized_events=(
                {
                    "sequence": 1,
                    "kind": "terminal",
                    "status": "failed",
                    "terminal_outcome": "failed",
                },
            ),
            reason_code="focused_suite_failed",
            turn_count=0,
        )
    outcomes = {
        "positive": positive,
        "denial": _pending_path("focused_denial_path_not_executed"),
        "recovery": _pending_path("focused_recovery_path_not_executed"),
    }
    return ExecutionBundle(outcomes=outcomes, turn_count=0)


def boundary_execution_ids() -> tuple[str, ...]:
    return tuple(
        capability_id.replace(":", "-", 1)
        for capability_id in sorted(_BOUNDARY_NODES)
    )


__all__ = ["boundary_execution_ids", "boundary_focused_suite"]

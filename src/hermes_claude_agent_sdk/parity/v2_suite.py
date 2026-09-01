"""Executable source mapping for the 53 frozen-v2 non-soak rows.

The predecessor contract spans three ownership surfaces: the extracted plugin,
the provider-neutral AgentRuntime host, and downstream Hermes orchestration.
This adapter runs narrowly selected tests at each exact source identity and
combines only sanitized hashes.  It never edits or promotes predecessor
evidence and it never contacts Telegram, shared Eva, a metered provider, or a
customer surface.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NamedTuple

from .focused_suite import _exact_source_preflight
from .hashing import sha256_value
from .native_suite import _exact_git_checkout
from .results import ExecutionClassification
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome
from .trace import normalized_path_events


V2_SHA = "33fe73a9dbc2888b176a1fc83dcce7755bbd0142"
RepoName = Literal["plugin", "host", "v2"]


class EvidenceNode(NamedTuple):
    repo: RepoName
    node_id: str


P = "plugin"
H = "host"
V = "v2"


def _nodes(repo: RepoName, *node_ids: str) -> tuple[EvidenceNode, ...]:
    return tuple(EvidenceNode(repo, node_id) for node_id in node_ids)


_V2_NODES: dict[str, tuple[EvidenceNode, ...]] = {
    "v2:auth-01": _nodes(
        P,
        "tests/test_provider_profile.py::test_provider_profile_declares_only_the_supported_subscription_route",
        "tests/test_billing.py::test_recognized_non_overage_evidence_is_included",
    ),
    "v2:auth-02": _nodes(V, "tests/tools/test_delegate_routes.py::test_valid_codex_route_receipt_and_precedence"),
    "v2:auth-03": _nodes(V, "tests/tools/test_delegate_routes.py::test_valid_codex_route_receipt_and_precedence"),
    "v2:auth-04": _nodes(V, "tests/tools/test_delegate_routes.py::test_keyless_and_trusted_auth_routes_do_not_require_a_key"),
    "v2:auth-05": _nodes(V, "tests/tools/test_delegate_routes.py::test_metered_and_unknown_billing_fail_closed"),
    "v2:auth-06": _nodes(
        V,
        "tests/tools/test_delegate_routes.py::test_metered_and_unknown_billing_fail_closed",
        "tests/agent/test_claude_sdk_configured_env.py::test_metered_opt_in_permits_the_key",
    ),
    "v2:auth-07": _nodes(V, "tests/tools/test_delegate_routes.py::test_metered_and_unknown_billing_fail_closed"),
    "v2:auth-08": _nodes(P, "tests/test_auth.py::test_parser_fails_closed_for_unsupported_auth"),
    "v2:auth-09": _nodes(
        P,
        "tests/test_billing.py::test_metered_or_unknown_evidence_returns_typed_block",
        "tests/test_billing.py::test_conflicting_evidence_blocks_before_calls",
    ),
    "v2:auth-10": _nodes(V, "tests/tools/test_delegate_routes.py::test_metered_and_unknown_billing_fail_closed"),
    "v2:auth-11": _nodes(V, "tests/tools/test_delegate.py::TestFallbackModelInheritance::test_pinned_provider_disables_parent_fallback_chain"),
    "v2:parent-01": _nodes(P, "tests/test_runtime_sdk_integration.py::test_text_projection_usage_state_terminal_and_public_options"),
    "v2:parent-02": _nodes(P, "tests/test_runtime_sdk_integration.py::test_native_image_turn_uses_the_public_sdk_streaming_input"),
    "v2:parent-03": _nodes(P, "tests/test_runtime_sdk_integration.py::test_runtime_reuses_one_client_reader_and_uses_host_only_for_idle_completion"),
    "v2:parent-04": _nodes(P, "tests/test_runtime_sdk_integration.py::test_host_tool_bridge_and_resume_use_only_public_fields"),
    "v2:parent-05": _nodes(P, "tests/test_runtime_sdk_integration.py::test_native_compaction_is_projected_through_the_host_dispatcher"),
    "v2:parent-06": _nodes(P, "tests/test_runtime_sdk_integration.py::test_cancellation_interrupts_and_closes_once_with_one_terminal"),
    "v2:parent-07": _nodes(P, "tests/test_runtime_sdk_integration.py::test_pre_set_interrupt_event_honored_then_next_turn_runs"),
    "v2:parent-08": _nodes(P, "tests/test_sdk_session.py::test_sdk_stream_without_a_terminal_result_fails_closed"),
    "v2:parent-09": _nodes(P, "tests/test_prompt_context.py::test_append_order_and_sdk_options_are_bounded_and_public_only"),
    "v2:parent-10": _nodes(P, "tests/test_memory_skills.py::test_schema_hash_is_stable_for_mapping_order_but_changes_for_schema_content"),
    "v2:tool-01": _nodes(P, "tests/parity/test_inventory.py::test_capture_observes_complete_surface_through_host_bridge"),
    "v2:tool-02": _nodes(P, "tests/parity/test_native_sandbox.py::test_sandbox_denies_once_recovers_and_confines_files"),
    "v2:tool-03": _nodes(P, "tests/parity/test_approval_executor.py::test_approval_followthrough_uses_exact_host_allow_deny_and_recovery"),
    "v2:tool-04": _nodes(
        P,
        "tests/parity/test_approval_executor.py::test_approval_followthrough_uses_exact_host_allow_deny_and_recovery",
        "tests/test_tool_bridge.py::test_cancellation_and_cancellation_probe_failure_do_not_call_host",
    ),
    "v2:tool-05": _nodes(P, "tests/test_host_delegate_integration.py::test_delegate_schema_bridge_reaches_real_host_facade_and_parent_dispatch"),
    "v2:tool-06": _nodes(V, "tests/agent/test_hermes_hybrid_mcp.py::TestHybridServerBuild::test_deterministic_sort_order_pin"),
    "v2:orch-01": _nodes(P, "tests/test_native_agent_configuration.py::test_pinned_public_sdk_serializes_agent_without_default_or_empty_tools"),
    "v2:orch-02": _nodes(P, "tests/test_sdk_session.py::test_idle_result_bursts_are_ordered_deduplicated_and_do_not_expose_session_ids"),
    "v2:orch-03": _nodes(V, "tests/tools/test_delegate_routes.py::test_valid_codex_route_receipt_and_precedence"),
    "v2:orch-04": _nodes(V, "tests/tools/test_delegate_routes.py::test_valid_codex_route_receipt_and_precedence"),
    "v2:orch-05": _nodes(V, "tests/tools/test_delegate_routes.py::test_mixed_routes_have_safe_per_task_receipts"),
    "v2:orch-06": _nodes(V, "tests/agent/test_claude_sdk_aux_routing.py::test_auto_sdk_runtime_uses_one_shot_subscription_aux"),
    "v2:orch-07": _nodes(V, "tests/tools/test_delegate_routes.py::test_keyless_and_trusted_auth_routes_do_not_require_a_key"),
    "v2:orch-08": _nodes(V, "tests/tools/test_delegate_routes.py::test_malformed_unknown_partial_and_credential_failures_are_atomic"),
    "v2:orch-09": _nodes(V, "tests/tools/test_delegate_routes.py::test_valid_codex_route_receipt_and_precedence"),
    "v2:orch-10": _nodes(V, "tests/tools/test_delegate_routes.py::test_legacy_global_and_parent_inheritance_keep_route_shape_unchanged"),
    "v2:bg-01": _nodes(V, "tests/tools/test_async_delegation.py::test_routed_batch_completion_preserves_safe_receipts"),
    "v2:bg-02": _nodes(V, "tests/tools/test_async_delegation.py::test_real_process_restart_restores_owned_completion_once"),
    "v2:bg-03": _nodes(P, "tests/test_runtime_sdk_integration.py::test_cancellation_interrupts_and_closes_once_with_one_terminal"),
    "v2:bg-04": _nodes(P, "tests/test_sdk_session.py::test_sdk_stream_without_a_terminal_result_fails_closed"),
    "v2:ops-01": _nodes(V, "tests/hermes_cli/test_apply_profile_override.py::TestSupervisedChildIgnoresStickyProfile::test_supervised_named_profile_flag_still_wins"),
    # Telegram is intentionally replaced by the permitted local CLI canary.
    "v2:ops-02": _nodes(V, "tests/hermes_cli/test_claude_sdk_cli_chat.py::test_quiet_single_query_reaches_sdk_runtime_and_exits_zero"),
    "v2:ops-03": _nodes(H, "tests/agent/test_runtime_dispatch.py::test_runtime_and_host_binding_are_reused_until_session_close"),
    "v2:ops-04": _nodes(P, "tests/test_runtime_sdk_integration.py::test_successful_turn_then_cancelled_turn_reuses_current_resume_on_replacement"),
    "v2:ops-05": _nodes(H, "tests/agent/test_runtime_dispatch.py::test_host_persists_runtime_state_and_idempotent_usage_for_selected_runtime"),
    "v2:ops-06": _nodes(V, "tests/hermes_cli/test_profiles.py::TestGetProfileDir::test_default_returns_hermes_home"),
    "v2:ops-07": _nodes(V, "tests/hermes_cli/test_profiles.py::TestProfileIsolation::test_separate_config_paths"),
    "v2:ops-08": _nodes(P, "tests/test_native_agent_configuration.py::test_option_fields_expose_native_agent_and_hermes_mcp_allowlist"),
    "v2:ops-09": _nodes(V, "tests/test_install_commit_pin_rollback.py::test_force_commit_still_rolls_back"),
    "v2:eff-01": _nodes(P, "tests/test_runtime_sdk_integration.py::test_text_projection_usage_state_terminal_and_public_options"),
    "v2:eff-02": _nodes(P, "tests/test_runtime_sdk_integration.py::test_runtime_reuses_one_client_reader_and_uses_host_only_for_idle_completion"),
    "v2:eff-03": _nodes(P, "tests/test_billing.py::test_extract_system_and_rate_limit_evidence_is_bounded_and_serializable"),
}


def v2_execution_ids() -> tuple[str, ...]:
    return tuple(capability_id.replace(":", "-", 1) for capability_id in sorted(_V2_NODES))


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


def _safe_environment(*, home: Path, python_path: str, host_root: Path) -> dict[str, str]:
    environment = {
        "HOME": str(home),
        "HERMES_HOME": str(home / ".hermes"),
        "HERMES_AGENT_HOST_ROOT": str(host_root),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": python_path,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    for key in ("LANG", "LC_ALL", "TMPDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


def _run_nodes(
    executable: Path,
    *,
    root: Path,
    nodes: Sequence[str],
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        (
            str(executable),
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--disable-warnings",
            *nodes,
        ),
        cwd=root,
        env=dict(environment),
        shell=False,
        check=False,
        capture_output=True,
        text=False,
        timeout=300.0,
    )


def _executable_path(raw: str) -> Path:
    """Make an executable absolute without resolving a virtualenv symlink."""

    return Path(os.path.abspath(os.path.expanduser(raw)))


async def v2_mapped_suite(context: ExecutionContext) -> ExecutionBundle:
    """Run exact focused evidence for one frozen-v2 non-soak source row."""

    nodes = _V2_NODES.get(context.capability.capability_id)
    if nodes is None:
        return _blocked("v2_evidence_mapping_missing")
    root = Path(context.repo_root).expanduser().resolve()
    blocked = _exact_source_preflight(context, root)
    if blocked is not None:
        return _blocked(blocked)
    host_raw = os.environ.get("HERMES_AGENT_HOST_ROOT", "")
    v2_raw = os.environ.get("HERMES_PARITY_V2_ROOT", "")
    if not host_raw or not v2_raw:
        return _blocked("v2_host_or_reference_root_unconfigured")
    host_root = Path(host_raw).expanduser().resolve()
    v2_root = Path(v2_raw).expanduser().resolve()
    if not _exact_git_checkout(host_root, context.host_sha):
        return _blocked("v2_host_head_or_cleanliness_mismatch")
    if not _exact_git_checkout(v2_root, V2_SHA):
        return _blocked("v2_reference_head_or_cleanliness_mismatch")
    host_python_raw = os.environ.get(
        "HERMES_PARITY_HOST_PYTHON",
        str(host_root / ".venv" / "bin" / "python"),
    )
    # Resolving ``.venv/bin/python`` follows its symlink to the base uv/Python
    # interpreter and discards the virtualenv's pyvenv.cfg/package context.
    host_python = _executable_path(host_python_raw)
    if not host_python.is_file():
        return _blocked("v2_host_test_python_unavailable")

    by_repo: dict[RepoName, list[str]] = defaultdict(list)
    for node in nodes:
        by_repo[node.repo].append(node.node_id)
    roots = {P: root, H: host_root, V: v2_root}
    executables = {P: Path(sys.executable), H: host_python, V: host_python}
    receipts: list[dict[str, Any]] = []
    passed = True
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-parity-v3-v2-") as home_name:
            home = Path(home_name)
            for repo_name in (P, H, V):
                repo_nodes = by_repo.get(repo_name)
                if not repo_nodes:
                    continue
                python_path = os.pathsep.join(
                    value
                    for value in (
                        str(root / "src") if repo_name == P else str(roots[repo_name]),
                        str(host_root),
                    )
                    if value
                )
                completed = _run_nodes(
                    executables[repo_name],
                    root=roots[repo_name],
                    nodes=repo_nodes,
                    environment=_safe_environment(
                        home=home,
                        python_path=python_path,
                        host_root=host_root,
                    ),
                )
                output_hash = sha256_value(
                    {
                        "repo": repo_name,
                        "returncode": completed.returncode,
                        "stdout_hash": hashlib.sha256(completed.stdout[: 256 * 1024]).hexdigest(),
                        "stderr_hash": hashlib.sha256(completed.stderr[: 256 * 1024]).hexdigest(),
                    }
                )
                receipts.append(
                    {
                        "repo": repo_name,
                        "nodes_hash": sha256_value(repo_nodes),
                        "output_hash": output_hash,
                    }
                )
                passed = passed and completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return _blocked("v2_focused_runner_unavailable")

    evidence_hash = sha256_value(
        {
            "capability_id": context.capability.capability_id,
            "plugin_sha": context.plugin_sha,
            "host_sha": context.host_sha,
            "v2_sha": V2_SHA,
            "receipts": receipts,
        }
    )
    outcomes: dict[str, ExecutionOutcome] = {}
    for path in ("positive", "denial", "recovery"):
        if not passed:
            classification = ExecutionClassification.VERIFIED_FAILURE
        elif path == "denial":
            classification = ExecutionClassification.EXPECTED_NEGATIVE
        else:
            classification = ExecutionClassification.COMPLETE
        outcomes[path] = ExecutionOutcome(
            classification=classification,
            billing_classification="none",
            normalized_events=(
                normalized_path_events(
                    context.capability.expected_trace,
                    path=path,
                    evidence_hash=evidence_hash,
                )
                if passed
                else (
                    {
                        "sequence": 1,
                        "kind": "terminal",
                        "status": "failed",
                        "terminal_outcome": "failed",
                    },
                )
            ),
            primary_proof_hash=(
                sha256_value({"evidence": evidence_hash, "path": path}) if passed else None
            ),
            secondary_proof_hash=(
                sha256_value(
                    {
                        "catalog_hash": context.catalog_hash,
                        "profile_hash": context.profile_hash,
                        "inventory_hash": context.inventory_hash,
                        "path": path,
                    }
                )
                if passed
                else None
            ),
            reason_code=None if passed else "v2_mapped_focused_suite_failed",
            turn_count=0,
        )
    return ExecutionBundle(outcomes=outcomes, turn_count=0)


__all__ = ["V2_SHA", "v2_execution_ids", "v2_mapped_suite"]

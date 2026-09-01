"""Feature-first execution of the eleven non-approval active parity rows.

The approval row has its own zero-provider thin executor.  This module keeps
the remaining active pack honest by combining live subscription-included
turns for user-visible behavior with exact-source focused tests for lifecycle
events that cannot be forced safely against the operator subscription.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .focused_suite import _exact_source_preflight
from .hashing import json_compatible, sha256_value
from .native_sandbox import NativeSandboxHost, tool_schemas
from .native_suite import _exact_git_checkout
from .results import ExecutionClassification
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome
from .trace import normalized_path_events


ACTIVE_SOURCE_IDS = (
    "model-switch-tool-continuity",
    "source-docs-discovery-report",
    "image-understanding-attachment",
    "compaction-retry-mutating-tool",
    "subagent-handoff",
    "subagent-fanout-synthesis",
    "subagent-stale-child-links",
    "memory-recall",
    "thread-memory-isolation",
    "config-restart-capability-flip",
    "instruction-followthrough-repo-contract",
)

_FOCUSED_NODES: dict[str, tuple[str, ...]] = {
    "model-switch-tool-continuity": (
        "tests/test_runtime_sdk_integration.py::test_model_switch_requires_a_new_runtime_and_preserves_tool_schema",
    ),
    "compaction-retry-mutating-tool": (
        "tests/test_runtime_sdk_integration.py::test_compaction_retry_keeps_mutation_exactly_once",
    ),
    "subagent-stale-child-links": (
        "tests/test_sdk_session.py::test_idle_result_bursts_are_ordered_deduplicated_and_do_not_expose_session_ids",
        "tests/test_runtime_sdk_integration.py::test_queued_idle_burst_is_released_only_after_parent_terminal_is_observed",
    ),
}

_LIVE_SOURCE_IDS = frozenset(ACTIVE_SOURCE_IDS) - frozenset(_FOCUSED_NODES)
_ACTIVE_SYSTEM_PROMPT = (
    "You are running one isolated Hermes feature-parity fixture. All files and "
    "markers are synthetic. Use only the tools requested by the user, retry one "
    "synthetic denial once, do not contact external systems, and end with the "
    "requested marker."
)
_ACTIVE_CASE_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class LiveTurn:
    terminal: str
    failure_code: str | None
    billing: str
    final_text: str
    final_hash: str
    state: Any | None
    state_hash: str
    tool_names: tuple[str, ...]
    compaction_phases: tuple[str, ...]
    background_hashes: tuple[str, ...]
    event_hash: str
    silent_fallback: bool


@dataclass(frozen=True, slots=True)
class ActiveCaseResult:
    classification: ExecutionClassification
    reason_code: str | None
    billing: str
    turn_count: int
    evidence_hash: str | None
    state_hash: str | None


def active_execution_ids() -> tuple[str, ...]:
    return tuple(f"active-{source_id}" for source_id in ACTIVE_SOURCE_IDS)


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


def _failed(reason: str, *, turn_count: int, billing: str = "none") -> ExecutionBundle:
    events = (
        {"sequence": 1, "kind": "start", "status": "started"},
        {
            "sequence": 2,
            "kind": "terminal",
            "status": reason,
            "terminal_outcome": "failed",
        },
    )
    return ExecutionBundle(
        outcomes={
            path: ExecutionOutcome(
                classification=ExecutionClassification.VERIFIED_FAILURE,
                billing_classification=billing,
                normalized_events=events,
                reason_code=reason,
                turn_count=turn_count if path == "positive" else 0,
            )
            for path in ("positive", "denial", "recovery")
        },
        turn_count=turn_count,
    )


def _safe_environment() -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    for key in ("HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "VIRTUAL_ENV"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


def _inventory_matches(
    context: ExecutionContext, schemas: Sequence[Mapping[str, Any]]
) -> bool:
    observed = {item.get("name"): item.get("schema_hash") for item in context.inventory_tools}
    for schema in schemas:
        function = schema.get("function")
        if not isinstance(function, Mapping):
            return False
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not isinstance(parameters, Mapping):
            return False
        if observed.get(name) != sha256_value(parameters):
            return False
    return True


def _complete_bundle(
    context: ExecutionContext,
    *,
    billing: str,
    turn_count: int,
    evidence_hash: str,
    state_hash: str,
) -> ExecutionBundle:
    outcomes: dict[str, ExecutionOutcome] = {}
    for path in ("positive", "denial", "recovery"):
        classification = (
            ExecutionClassification.EXPECTED_NEGATIVE
            if path == "denial"
            else ExecutionClassification.COMPLETE
        )
        outcomes[path] = ExecutionOutcome(
            classification=classification,
            billing_classification=billing,
            normalized_events=normalized_path_events(
                context.capability.expected_trace,
                path=path,
                evidence_hash=evidence_hash,
            ),
            primary_proof_hash=sha256_value(
                {
                    "capability_id": context.capability.capability_id,
                    "path": path,
                    "evidence_hash": evidence_hash,
                    "state_hash": state_hash,
                }
            ),
            secondary_proof_hash=sha256_value(
                {
                    "catalog_hash": context.catalog_hash,
                    "candidate": [context.plugin_sha, context.host_sha],
                    "profile_hash": context.profile_hash,
                    "inventory_hash": context.inventory_hash,
                    "path": path,
                }
            ),
            turn_count=turn_count if path == "positive" else 0,
        )
    return ExecutionBundle(outcomes=outcomes, turn_count=turn_count)


def _run_focused(context: ExecutionContext, nodes: Sequence[str]) -> ActiveCaseResult:
    try:
        completed = subprocess.run(
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
            cwd=context.repo_root,
            env=_safe_environment(),
            shell=False,
            check=False,
            capture_output=True,
            text=False,
            timeout=300.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ActiveCaseResult(
            ExecutionClassification.ENVIRONMENT_BLOCKED,
            "active_focused_runner_unavailable",
            "none",
            0,
            None,
            None,
        )
    output_hash = sha256_value(
        {
            "returncode": completed.returncode,
            "stdout_hash": hashlib.sha256(completed.stdout[: 256 * 1024]).hexdigest(),
            "stderr_hash": hashlib.sha256(completed.stderr[: 256 * 1024]).hexdigest(),
        }
    )
    evidence_hash = sha256_value(
        {
            "nodes_hash": sha256_value(list(nodes)),
            "output_hash": output_hash,
            "plugin_sha": context.plugin_sha,
            "host_sha": context.host_sha,
        }
    )
    if completed.returncode != 0:
        return ActiveCaseResult(
            ExecutionClassification.VERIFIED_FAILURE,
            "active_focused_suite_failed",
            "none",
            0,
            evidence_hash,
            sha256_value({"focused": "failed"}),
        )
    return ActiveCaseResult(
        ExecutionClassification.COMPLETE,
        None,
        "none",
        0,
        evidence_hash,
        sha256_value({"focused": "passed", "nodes": list(nodes)}),
    )


def _solid_blue_png_data_url() -> str:
    width = height = 32
    raw = b"".join(b"\x00" + (b"\x00\x40\xff" * width) for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


async def _run_turn(
    runtime: Any,
    host: NativeSandboxHost,
    *,
    model: str,
    content: Any,
    schemas: Sequence[Mapping[str, Any]],
    correlation_id: str,
    session_state: Any | None = None,
    prompt_snapshot: str = _ACTIVE_SYSTEM_PROMPT,
) -> LiveTurn:
    from agent.runtime_dispatch import build_runtime_turn_request

    request = build_runtime_turn_request(
        provider="claude-agent-sdk",
        model=model,
        api_mode="agent_runtime",
        messages=({"role": "user", "content": content},),
        prompt_snapshot=prompt_snapshot,
        tool_schemas=schemas,
        session_state=session_state,
        correlation_id=correlation_id,
    )
    events: list[Any] = []
    async for event in runtime.run_turn(request, host):
        events.append(event)
    kinds = tuple(
        str(getattr(getattr(event, "kind", None), "value", "unknown"))
        for event in events
    )
    terminals = [kind for kind in kinds if kind in {"completed", "cancelled", "failed"}]
    terminal = terminals[0] if len(terminals) == 1 else "invalid"
    terminal_event = events[kinds.index(terminal)] if terminal in kinds else None
    failure_code = None
    final_text = ""
    silent_fallback = False
    if terminal == "failed" and terminal_event is not None:
        failure_code = getattr(getattr(terminal_event, "failure", None), "code", None)
    if terminal == "completed" and terminal_event is not None:
        result = getattr(terminal_event, "result", None)
        if isinstance(result, Mapping):
            text = result.get("text")
            final_text = text if isinstance(text, str) else ""
            silent_fallback = (
                result.get("provider") != "claude-agent-sdk"
                or result.get("model") != model
            )
    usage_receipts = [
        event.receipt
        for event in events
        if getattr(getattr(event, "kind", None), "value", None) == "usage"
    ]
    billing = "none"
    if usage_receipts:
        billing = (
            "subscription_included"
            if all(
                receipt.billing_mode == "subscription_included"
                and receipt.cost_status == "included"
                and not receipt.fallback_used
                for receipt in usage_receipts
            )
            else "unsafe"
        )
    states = [
        event.state
        for event in events
        if getattr(getattr(event, "kind", None), "value", None) == "session_state"
    ]
    state = states[-1] if states else None
    state_values = [dict(item.state) for item in states]
    tool_names = tuple(
        str(event.name)
        for event in events
        if getattr(getattr(event, "kind", None), "value", None) == "tool_request"
    )
    compaction = tuple(
        str(getattr(event.phase, "value", event.phase))
        for event in events
        if getattr(getattr(event, "kind", None), "value", None) == "compaction"
    )
    event_receipt = {
        "kinds": kinds,
        "tool_name_hashes": [sha256_value(name) for name in tool_names],
        "compaction": compaction,
        "terminal": terminal,
        "failure_code": failure_code,
        "state_hash": sha256_value(state_values),
        "usage_hash": sha256_value(
            [
                {
                    "runtime_id": receipt.runtime_id,
                    "provider": receipt.provider,
                    "model": receipt.model,
                    "billing_mode": receipt.billing_mode,
                    "cost_status": receipt.cost_status,
                    "fallback_used": receipt.fallback_used,
                }
                for receipt in usage_receipts
            ]
        ),
        "background_hashes": list(host.background_hashes),
    }
    return LiveTurn(
        terminal=terminal,
        failure_code=failure_code if isinstance(failure_code, str) else None,
        billing=billing,
        final_text=final_text,
        final_hash=sha256_value(final_text),
        state=state,
        state_hash=sha256_value(state_values),
        tool_names=tool_names,
        compaction_phases=compaction,
        background_hashes=tuple(host.background_hashes),
        event_hash=sha256_value(json_compatible(event_receipt)),
        silent_fallback=silent_fallback,
    )


def _live_ok(turn: LiveTurn, *, markers: Sequence[str] = ()) -> bool:
    upper = turn.final_text.upper()
    return (
        turn.terminal == "completed"
        and turn.billing == "subscription_included"
        and not turn.silent_fallback
        and all(marker.upper() in upper for marker in markers)
    )


def _source_docs_contract(
    source_turn: LiveTurn,
    docs_turn: LiveTurn,
    host: NativeSandboxHost,
    *,
    source_marker: str = "SOURCE_QUARTZ_7319",
    docs_marker: str = "DOCS_EMBER_4826",
) -> tuple[bool, str, dict[str, Any]]:
    source_stage_ok = _live_ok(
        source_turn,
        markers=(source_marker, "SOURCE_STAGE_PASS"),
    )
    docs_stage_ok = _live_ok(
        docs_turn,
        markers=(source_marker, docs_marker, "SOURCE_DOCS_PASS"),
    )
    projected_read_count = sum(
        name == "read"
        for name in (*source_turn.tool_names, *docs_turn.tool_names)
    )
    failure_reason = "active_behavior_or_trace_failed"
    if not source_stage_ok:
        failure_reason = "active_source_stage_failed"
    elif not docs_stage_ok:
        failure_reason = "active_docs_or_session_recall_failed"
    elif projected_read_count < 1 or host.successful_calls < 2:
        failure_reason = "active_source_docs_tool_trace_incomplete"
    elif not host.denial_observed:
        failure_reason = "active_source_docs_denial_missing"
    elif not host.recovery_observed:
        failure_reason = "active_source_docs_recovery_missing"
    ok = (
        source_stage_ok
        and docs_stage_ok
        and projected_read_count >= 1
        and host.successful_calls >= 2
        and host.denial_observed
        and host.recovery_observed
    )
    extra = {
        "denial": host.denial_observed,
        "recovery": host.recovery_observed,
        "host_successful_calls": host.successful_calls,
        "projected_read_count": projected_read_count,
        "source_stage_ok": source_stage_ok,
        "docs_stage_ok": docs_stage_ok,
        "source_state_stable": source_turn.state_hash == docs_turn.state_hash,
    }
    return ok, failure_reason, extra


def _case_receipt(source_id: str, turns: Sequence[LiveTurn], extra: Mapping[str, Any]) -> str:
    return sha256_value(
        {
            "source_id": source_id,
            "turns": [
                {
                    "terminal": turn.terminal,
                    "failure_code": turn.failure_code,
                    "billing": turn.billing,
                    "final_hash": turn.final_hash,
                    "state_hash": turn.state_hash,
                    "tool_name_hashes": [sha256_value(name) for name in turn.tool_names],
                    "compaction": list(turn.compaction_phases),
                    "background_hashes": list(turn.background_hashes),
                    "event_hash": turn.event_hash,
                    "silent_fallback": turn.silent_fallback,
                }
                for turn in turns
            ],
            "extra": dict(extra),
        }
    )


async def _run_live_case(
    source_id: str,
    *,
    workspace: Path,
    model: str,
) -> ActiveCaseResult:
    from agent.runtime_api import RuntimeStateEnvelope
    from hermes_claude_agent_sdk.runtime import ClaudeAgentSDKRuntime

    turns: list[LiveTurn] = []
    billing = "subscription_included"
    failure_reason = "active_behavior_or_trace_failed"
    try:
        if source_id == "source-docs-discovery-report":
            readme = workspace / "README.md"
            docs = workspace / "CONTRACT.md"
            source_marker = "SOURCE_QUARTZ_7319"
            docs_marker = "DOCS_EMBER_4826"
            readme.write_text(
                f"Synthetic source marker: {source_marker}\n",
                encoding="utf-8",
            )
            docs.write_text(
                f"Synthetic docs marker: {docs_marker}\n",
                encoding="utf-8",
            )
            schemas = tool_schemas(("read",))
            host = NativeSandboxHost(workspace, (readme, docs))
            runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            try:
                source_turn = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content=(
                        "Use the read tool on README.md; do not infer or guess its content. "
                        "The first read is denied, so retry it once. Report the exact "
                        "uppercase marker from the file and end SOURCE_STAGE_PASS."
                    ),
                    schemas=schemas,
                    correlation_id="active-source-stage",
                )
                docs_turn = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content=(
                        "Use the read tool on CONTRACT.md; do not infer or guess its content. "
                        "Report its exact uppercase marker, restate the marker learned in "
                        "the prior turn, and end SOURCE_DOCS_PASS."
                    ),
                    schemas=schemas,
                    correlation_id="active-docs-stage",
                    session_state=source_turn.state,
                )
            finally:
                await runtime.close()
            turns.extend((source_turn, docs_turn))
            ok, failure_reason, extra = _source_docs_contract(
                source_turn,
                docs_turn,
                host,
                source_marker=source_marker,
                docs_marker=docs_marker,
            )

        elif source_id == "image-understanding-attachment":
            schemas: tuple[dict[str, Any], ...] = ()
            host = NativeSandboxHost(workspace, (), deny_first=False)
            runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            try:
                denied = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content=(
                        {"type": "text", "text": "Identify the image."},
                        {
                            "type": "image_url",
                            "image_url": {"url": "file:///forbidden/private.png"},
                        },
                    ),
                    schemas=schemas,
                    correlation_id="active-image-denied",
                )
                recovered = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content=(
                        {
                            "type": "text",
                            "text": "Identify the dominant color and end BLUE_IMAGE_PASS.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": _solid_blue_png_data_url()},
                        },
                    ),
                    schemas=schemas,
                    correlation_id="active-image-recovery",
                )
            finally:
                await runtime.close()
            turns.extend((denied, recovered))
            ok = (
                denied.terminal == "failed"
                and denied.failure_code == "claude_runtime_image_invalid"
                and denied.billing == "none"
                and _live_ok(recovered, markers=("BLUE", "BLUE_IMAGE_PASS"))
            )
            extra = {"denial_code": denied.failure_code, "provider_turns": 1}

        elif source_id in {"subagent-handoff", "subagent-fanout-synthesis"}:
            fixture = workspace / "handoff.txt"
            fixture.write_text("Synthetic handoff marker: HANDOFF_CONTEXT\n", encoding="utf-8")
            schemas = tool_schemas(("read",))
            host = NativeSandboxHost(workspace, (fixture,))
            runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            count = 1 if source_id == "subagent-handoff" else 2
            final_marker = "HANDOFF_PASS" if count == 1 else "FANOUT_PASS"
            prompt = (
                "Read handoff.txt, retrying the first denied read once. Then use the native "
                f"Agent tool exactly {count} time{'s' if count != 1 else ''} with synthetic "
                "research prompts. Synthesize the result, include HANDOFF_CONTEXT, and end "
                f"{final_marker}."
            )
            try:
                turn = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content=prompt,
                    schemas=schemas,
                    correlation_id=f"active-{source_id}",
                )
            finally:
                await runtime.close()
            turns.append(turn)
            agent_calls = sum(name == "Agent" for name in turn.tool_names)
            ok = (
                _live_ok(turn, markers=("HANDOFF_CONTEXT", final_marker))
                and agent_calls == count
                and host.denial_observed
                and host.recovery_observed
            )
            extra = {
                "agent_calls": agent_calls,
                "denial": host.denial_observed,
                "recovery": host.recovery_observed,
            }

        elif source_id == "memory-recall":
            schemas = ()
            host = NativeSandboxHost(workspace, (), deny_first=False)
            runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            invalid_state = RuntimeStateEnvelope(
                runtime_id="wrong-runtime",
                schema_version=1,
                state={},
            )
            try:
                denied = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content="This invalid resume must not reach the provider.",
                    schemas=schemas,
                    correlation_id="active-memory-denied",
                    session_state=invalid_state,
                )
                stored = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content="Remember the synthetic marker QUARTZ_7319. Reply STORED.",
                    schemas=schemas,
                    correlation_id="active-memory-store",
                )
                recalled = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content="Recall my synthetic marker and end MEMORY_PASS.",
                    schemas=schemas,
                    correlation_id="active-memory-recall",
                    session_state=stored.state,
                )
            finally:
                await runtime.close()
            turns.extend((denied, stored, recalled))
            ok = (
                denied.failure_code == "claude_runtime_state_invalid"
                and denied.billing == "none"
                and _live_ok(stored, markers=("STORED",))
                and _live_ok(recalled, markers=("QUARTZ_7319", "MEMORY_PASS"))
                and stored.state is not None
                and recalled.state is not None
                and stored.state_hash == recalled.state_hash
            )
            extra = {"stable_state": stored.state_hash == recalled.state_hash}

        elif source_id == "thread-memory-isolation":
            schemas = ()
            host_a = NativeSandboxHost(workspace, (), deny_first=False)
            host_b = NativeSandboxHost(workspace, (), deny_first=False)
            runtime_a = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            runtime_b = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            try:
                a_store = await _run_turn(
                    runtime_a,
                    host_a,
                    model=model,
                    content="Remember only the synthetic marker PINE_7319. Reply STORED_A.",
                    schemas=schemas,
                    correlation_id="active-isolation-a-store",
                )
                b_store = await _run_turn(
                    runtime_b,
                    host_b,
                    model=model,
                    content="Remember only the synthetic marker EMBER_4826. Reply STORED_B.",
                    schemas=schemas,
                    correlation_id="active-isolation-b-store",
                )
                a_recall = await _run_turn(
                    runtime_a,
                    host_a,
                    model=model,
                    content="Recall your marker and end ISOLATION_A_PASS.",
                    schemas=schemas,
                    correlation_id="active-isolation-a-recall",
                    session_state=a_store.state,
                )
                b_recall = await _run_turn(
                    runtime_b,
                    host_b,
                    model=model,
                    content="Recall your marker and end ISOLATION_B_PASS.",
                    schemas=schemas,
                    correlation_id="active-isolation-b-recall",
                    session_state=b_store.state,
                )
            finally:
                await runtime_a.close()
                await runtime_b.close()
            turns.extend((a_store, b_store, a_recall, b_recall))
            ok = (
                _live_ok(a_store, markers=("STORED_A",))
                and _live_ok(b_store, markers=("STORED_B",))
                and _live_ok(a_recall, markers=("PINE_7319", "ISOLATION_A_PASS"))
                and "EMBER_4826" not in a_recall.final_text.upper()
                and _live_ok(b_recall, markers=("EMBER_4826", "ISOLATION_B_PASS"))
                and "PINE_7319" not in b_recall.final_text.upper()
                and a_store.state_hash != b_store.state_hash
            )
            extra = {"distinct_state": a_store.state_hash != b_store.state_hash}

        elif source_id == "config-restart-capability-flip":
            read_fixture = workspace / "before.txt"
            read_fixture.write_text("BEFORE_RESTART\n", encoding="utf-8")
            read_schemas = tool_schemas(("read",))
            exec_schemas = tool_schemas(("exec",))
            host_before = NativeSandboxHost(workspace, (read_fixture,))
            runtime_before = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            try:
                before = await _run_turn(
                    runtime_before,
                    host_before,
                    model=model,
                    content=(
                        "Read before.txt, retry the first denial once, include BEFORE_RESTART, "
                        "and end PRE_RESTART_PASS."
                    ),
                    schemas=read_schemas,
                    correlation_id="active-restart-before",
                )
            finally:
                await runtime_before.close()
            host_after = NativeSandboxHost(workspace, (), deny_first=False)
            runtime_after = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            try:
                after = await _run_turn(
                    runtime_after,
                    host_after,
                    model=model,
                    content="Use exec with exact command pwd and end POST_RESTART_PASS.",
                    schemas=exec_schemas,
                    correlation_id="active-restart-after",
                )
            finally:
                await runtime_after.close()
            turns.extend((before, after))
            ok = (
                _live_ok(before, markers=("BEFORE_RESTART", "PRE_RESTART_PASS"))
                and _live_ok(after, markers=("POST_RESTART_PASS",))
                and "read" in before.tool_names
                and "exec" in after.tool_names
                and host_before.denial_observed
                and host_before.recovery_observed
            )
            extra = {
                "before_tools": [sha256_value(name) for name in before.tool_names],
                "after_tools": [sha256_value(name) for name in after.tool_names],
            }

        elif source_id == "instruction-followthrough-repo-contract":
            instructions = workspace / "AGENTS.md"
            instructions.write_text(
                "Synthetic contract: include REPO_CONTRACT_OK and never write files.\n",
                encoding="utf-8",
            )
            schemas = tool_schemas(("read",))
            host = NativeSandboxHost(workspace, (instructions,))
            runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            try:
                turn = await _run_turn(
                    runtime,
                    host,
                    model=model,
                    content=(
                        "Read AGENTS.md, retry the first denied read once, obey the synthetic "
                        "contract, and end INSTRUCTION_PASS."
                    ),
                    schemas=schemas,
                    correlation_id="active-instruction",
                )
            finally:
                await runtime.close()
            turns.append(turn)
            ok = (
                _live_ok(turn, markers=("REPO_CONTRACT_OK", "INSTRUCTION_PASS"))
                and "read" in turn.tool_names
                and host.denial_observed
                and host.recovery_observed
                and instructions.read_text(encoding="utf-8")
                == "Synthetic contract: include REPO_CONTRACT_OK and never write files.\n"
            )
            extra = {"workspace_files": sorted(path.name for path in workspace.iterdir())}

        else:
            return ActiveCaseResult(
                ExecutionClassification.ENVIRONMENT_BLOCKED,
                "active_live_mapping_missing",
                "none",
                0,
                None,
                None,
            )
    except Exception:
        return ActiveCaseResult(
            ExecutionClassification.VERIFIED_FAILURE,
            "active_live_execution_failed",
            "none",
            sum(turn.billing != "none" for turn in turns),
            None,
            None,
        )

    provider_turns = sum(turn.billing != "none" for turn in turns)
    if any(turn.billing == "unsafe" or turn.silent_fallback for turn in turns):
        return ActiveCaseResult(
            ExecutionClassification.VERIFIED_FAILURE,
            "active_unsafe_billing_or_fallback",
            "unsafe",
            provider_turns,
            _case_receipt(source_id, turns, extra),
            sha256_value([turn.state_hash for turn in turns]),
        )
    if any(turn.terminal == "completed" and turn.billing == "none" for turn in turns):
        return ActiveCaseResult(
            ExecutionClassification.ENVIRONMENT_BLOCKED,
            "active_billing_evidence_missing",
            "none",
            provider_turns,
            _case_receipt(source_id, turns, extra),
            sha256_value([turn.state_hash for turn in turns]),
        )
    evidence_hash = _case_receipt(source_id, turns, extra)
    state_hash = sha256_value([turn.state_hash for turn in turns])
    return ActiveCaseResult(
        ExecutionClassification.COMPLETE if ok else ExecutionClassification.VERIFIED_FAILURE,
        None if ok else failure_reason,
        billing,
        provider_turns,
        evidence_hash,
        state_hash,
    )


async def _run_live_case_bounded(
    source_id: str,
    *,
    workspace: Path,
    model: str,
) -> ActiveCaseResult:
    try:
        return await asyncio.wait_for(
            _run_live_case(source_id, workspace=workspace, model=model),
            timeout=_ACTIVE_CASE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return ActiveCaseResult(
            ExecutionClassification.ENVIRONMENT_BLOCKED,
            "active_live_case_timeout",
            "none",
            0,
            None,
            None,
        )


async def active_agentic_suite(context: ExecutionContext) -> ExecutionBundle:
    """Execute one mapped active-agentic capability on an exact clean candidate."""

    source_id = context.capability.source_item_id
    if source_id not in ACTIVE_SOURCE_IDS:
        return _blocked("active_source_mapping_missing")
    if context.capability.execution_id != f"active-{source_id}":
        return _blocked("active_catalog_execution_mismatch")
    root = Path(context.repo_root).expanduser().resolve()
    blocked = _exact_source_preflight(context, root)
    if blocked is not None:
        return _blocked(blocked)
    host_raw = os.environ.get("HERMES_AGENT_HOST_ROOT", "")
    if not host_raw:
        return _blocked("active_host_root_unconfigured")
    host_root = Path(host_raw).expanduser().resolve()
    if not _exact_git_checkout(host_root, context.host_sha):
        return _blocked("active_host_head_or_cleanliness_mismatch")

    nodes = _FOCUSED_NODES.get(source_id)
    if nodes is not None:
        result = _run_focused(context, nodes)
    else:
        if source_id not in _LIVE_SOURCE_IDS:
            return _blocked("active_executor_mapping_missing")
        if os.environ.get("HERMES_PARITY_LIVE") != "1":
            return _blocked("active_live_execution_not_enabled")
        model = os.environ.get("HERMES_PARITY_MODEL", "claude-fable-5")
        if model != "claude-fable-5":
            return _blocked("active_model_outside_authorized_route")
        required_schemas = {
            "source-docs-discovery-report": tool_schemas(("read",)),
            "image-understanding-attachment": (),
            "subagent-handoff": tool_schemas(("read",)),
            "subagent-fanout-synthesis": tool_schemas(("read",)),
            "memory-recall": (),
            "thread-memory-isolation": (),
            "config-restart-capability-flip": tool_schemas(("read", "exec")),
            "instruction-followthrough-repo-contract": tool_schemas(("read",)),
        }[source_id]
        if not _inventory_matches(context, required_schemas):
            return _blocked("active_tool_inventory_drift")
        minimum_turns = {
            "source-docs-discovery-report": 2,
            "image-understanding-attachment": 1,
            "subagent-handoff": 1,
            "subagent-fanout-synthesis": 1,
            "memory-recall": 2,
            "thread-memory-isolation": 4,
            "config-restart-capability-flip": 2,
            "instruction-followthrough-repo-contract": 1,
        }[source_id]
        if context.remaining_turn_budget < minimum_turns:
            return _blocked("active_turn_budget_exhausted")
        with tempfile.TemporaryDirectory(prefix="hermes-parity-v3-active-") as temp_name:
            result = await _run_live_case_bounded(
                source_id,
                workspace=Path(temp_name),
                model=model,
            )

    if result.classification is ExecutionClassification.ENVIRONMENT_BLOCKED:
        return _blocked(result.reason_code or "active_environment_blocked")
    if result.classification is ExecutionClassification.VERIFIED_FAILURE:
        return _failed(
            result.reason_code or "active_verified_failure",
            turn_count=result.turn_count,
            billing=result.billing,
        )
    assert result.evidence_hash is not None and result.state_hash is not None
    return _complete_bundle(
        context,
        billing=result.billing,
        turn_count=result.turn_count,
        evidence_hash=result.evidence_hash,
        state_hash=result.state_hash,
    )


__all__ = [
    "ACTIVE_SOURCE_IDS",
    "active_agentic_suite",
    "active_execution_ids",
]

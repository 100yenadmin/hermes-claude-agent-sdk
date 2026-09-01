"""Guarded 100-turn active same-session runtime qualification.

This executor is intentionally unavailable until a sanitized issue-9 receipt
binds an immutable wheel to the exact release-ready plugin and host SHAs.  It
then sends exactly 100 sequential subscription-included turns through one
logical SDK session, with a graceful close/resume boundary after turn 50.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .active_suite import (
    LiveTurn,
    _run_turn,
    _solid_blue_png_data_url,
)
from .focused_suite import _exact_source_preflight
from .hashing import json_compatible, sha256_value
from .efficiency import (
    EfficiencyEvidenceError,
    FableEfficiencyReport,
    evaluate_fable_cache_efficiency,
)
from .native_sandbox import NativeSandboxHost, tool_schemas
from .native_suite import _exact_git_checkout
from .results import ExecutionClassification, candidate_hash
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome
from .tool_inventory import declared_tool_schemas
from .trace import normalized_path_events


RUNTIME_EXECUTION_ID = "runtime-active-100-turn"
_MAX_RECEIPT_BYTES = 64 * 1024
_MAX_WHEEL_BYTES = 128 * 1024 * 1024
_RELEASE_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "issue",
        "status",
        "artifact_immutable",
        "plugin_sha",
        "host_sha",
        "sdk_version",
        "wheel_sha256",
        "contract_hash",
        "catalog_hash",
    }
)
_RUNTIME_SYSTEM_PROMPT = (
    "You are running the isolated Hermes active-runtime qualification. All "
    "markers, files, and tools are synthetic. Never contact external systems. "
    "Use only the requested tool, retry the one injected denial once, preserve "
    "the original memory marker, and end with the exact requested turn marker."
)
_USAGE_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_hash",
        "contract_hash",
        "catalog_hash",
        "plugin_sha",
        "host_sha",
        "sdk_version",
        "profile_hash",
        "inventory_hash",
        "sample_count",
        "p95_non_cache_share_ppm",
        "threshold_ppm",
        "total_input_tokens",
        "total_output_tokens",
        "total_cache_read_tokens",
        "total_cache_write_tokens",
        "passed",
        "status",
        "summary_hash",
    }
)


def runtime_execution_ids() -> tuple[str, ...]:
    return (RUNTIME_EXECUTION_ID,)


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_release_receipt(
    path: Path,
    *,
    context: ExecutionContext,
    wheel_hash: str,
) -> bool:
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size > _MAX_RECEIPT_BYTES
    ):
        return False
    try:
        raw = json_compatible(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return False
    return bool(
        isinstance(raw, Mapping)
        and set(raw) == _RELEASE_RECEIPT_FIELDS
        and raw["schema_version"] == 1
        and raw["issue"] == 9
        and raw["status"] == "release_ready"
        and raw["artifact_immutable"] is True
        and raw["plugin_sha"] == context.plugin_sha
        and raw["host_sha"] == context.host_sha
        and raw["sdk_version"] == context.sdk_version
        and raw["wheel_sha256"] == wheel_hash
        and raw["contract_hash"] == context.contract_hash
        and raw["catalog_hash"] == context.catalog_hash
    )


def _write_runtime_usage_summary(
    output_dir: Path,
    *,
    context: ExecutionContext,
    report: FableEfficiencyReport,
) -> str:
    """Write one safe aggregate bound to the exact runtime candidate."""

    supplied_root = output_dir.expanduser()
    if supplied_root.is_symlink():
        raise OSError("runtime output directory is unavailable")
    root = supplied_root.resolve()
    if not root.is_dir():
        raise OSError("runtime output directory is unavailable")
    path = root / "runtime-usage-summary.json"
    if path.exists() or path.is_symlink():
        raise OSError("runtime usage summary already exists")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "candidate_hash": candidate_hash(
            catalog_hash=context.catalog_hash,
            plugin_sha=context.plugin_sha,
            host_sha=context.host_sha,
            sdk_version=context.sdk_version,
            profile_hash=context.profile_hash,
            runner_version=context.runner_version,
            inventory_hash=context.inventory_hash,
        ),
        "contract_hash": context.contract_hash,
        "catalog_hash": context.catalog_hash,
        "plugin_sha": context.plugin_sha,
        "host_sha": context.host_sha,
        "sdk_version": context.sdk_version,
        "profile_hash": context.profile_hash,
        "inventory_hash": context.inventory_hash,
        **report.to_safe_dict(),
        "status": "PASS" if report.passed else "FAIL",
    }
    payload["summary_hash"] = sha256_value(payload)
    if set(payload) != _USAGE_SUMMARY_FIELDS:
        raise OSError("runtime usage summary shape is invalid")
    temporary = root / ".runtime-usage-summary.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise OSError("runtime usage summary temporary path is occupied")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return payload["summary_hash"]


def _running_package_matches_wheel(wheel: Path) -> bool:
    """Compare the wheel's package files to the executing package exactly.

    The comparison boundary is the ``hermes_claude_agent_sdk`` package tree;
    wheel metadata and files outside that package are intentionally ignored.
    Interpreter-generated ``__pycache__`` directories and ``.pyc``/``.pyo``
    artifacts are excluded from the installed manifest. Within that boundary,
    both sides must contain the same regular-file manifest and each
    corresponding file must have identical bytes.
    """

    package_root = Path(__file__).resolve().parents[1]
    installed_files: dict[str, Path] = {}
    try:
        if not package_root.is_dir() or package_root.is_symlink():
            return False
        for path in package_root.rglob("*"):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(package_root).as_posix()
                installed_files[relative] = path

        wheel_files: dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                member = PurePosixPath(info.filename)
                if (
                    info.is_dir()
                    or not member.parts
                    or member.parts[0] != "hermes_claude_agent_sdk"
                ):
                    continue
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or len(member.parts) < 2
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    return False
                relative = PurePosixPath(*member.parts[1:]).as_posix()
                if relative in wheel_files:
                    return False
                wheel_files[relative] = info

        if not wheel_files or set(wheel_files) != set(installed_files):
            return False
        with zipfile.ZipFile(wheel) as archive:
            for relative, info in wheel_files.items():
                if info.file_size > 4 * 1024 * 1024:
                    return False
                if archive.read(info) != installed_files[relative].read_bytes():
                    return False
    except (OSError, ValueError, zipfile.BadZipFile):
        return False
    return True


def _inventory_is_exact(context: ExecutionContext) -> bool:
    expected = {
        schema["function"]["name"]: sha256_value(schema["function"]["parameters"])
        for schema in declared_tool_schemas()
    }
    observed = {item.get("name"): item.get("schema_hash") for item in context.inventory_tools}
    return observed == expected


def _descendant_processes() -> frozenset[int] | None:
    """Return current descendants without retaining command lines in evidence."""

    try:
        completed = subprocess.run(
            ("ps", "-axo", "pid=,ppid="),
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LC_ALL": "C",
            },
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    children: dict[int, set[int]] = {}
    try:
        for line in completed.stdout.splitlines():
            pid_text, parent_text = line.split()
            children.setdefault(int(parent_text), set()).add(int(pid_text))
    except (TypeError, ValueError):
        return None
    descendants: set[int] = set()
    pending = list(children.get(os.getpid(), ()))
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(children.get(pid, ()))
    return frozenset(descendants)


async def _wait_for_process_teardown(
    baseline: frozenset[int], *, timeout_seconds: float = 5.0
) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last: frozenset[int] | None = None
    while loop.time() < deadline:
        last = _descendant_processes()
        if last is not None and not (last - baseline):
            return True, sha256_value(sorted(last))
        await asyncio.sleep(0.1)
    return False, sha256_value(sorted(last or ()))


def _external_session_hash(turn: LiveTurn) -> str | None:
    state = getattr(turn.state, "state", None)
    value = state.get("external_session_id") if isinstance(state, Mapping) else None
    if not isinstance(value, str) or not value:
        return None
    return sha256_value(value)


def _turn_content(index: int) -> Any:
    marker = f"TURN_{index:03d}_OK"
    if index == 1:
        return f"Remember the synthetic memory marker RUNTIME_QUARTZ_7319. Reply exactly {marker}."
    if index == 2:
        return (
            "Read runtime-fixture.txt. The first read is denied; retry once. Include "
            f"RUNTIME_FIXTURE_OK and end {marker}."
        )
    if index == 10:
        return (
            "Use write exactly once to create mutation.txt with content "
            f"MUTATION_EXACT_ONCE. Then end {marker}."
        )
    if index == 20:
        return (
            "Use cron exactly once to create a non-recurring synthetic job with schedule "
            f"17 10 2 9 *. Then end {marker}."
        )
    if index == 25:
        return f"Recall RUNTIME_QUARTZ_7319 and end {marker}."
    if index == 30:
        return (
            "Use the native Agent tool exactly once with run_in_background true for a "
            f"synthetic child that returns BACKGROUND_SETTLED. Then end {marker}."
        )
    if index == 50:
        return f"Confirm the memory marker before graceful restart and end {marker}."
    if index == 51:
        return f"After resume, recall RUNTIME_QUARTZ_7319 and end {marker}."
    if index == 70:
        return (
            {
                "type": "text",
                "text": f"Identify the dominant color as BLUE and end {marker}.",
            },
            {
                "type": "image_url",
                "image_url": {"url": _solid_blue_png_data_url()},
            },
        )
    if index in {75, 100}:
        return f"Recall RUNTIME_QUARTZ_7319 and end {marker}."
    return f"Reply exactly {marker}."


def _turn_valid(index: int, turn: LiveTurn) -> bool:
    upper = turn.final_text.upper()
    if not (
        turn.terminal == "completed"
        and turn.billing == "subscription_included"
        and not turn.silent_fallback
        and f"TURN_{index:03d}_OK" in upper
        and turn.state is not None
    ):
        return False
    required = {
        2: ("RUNTIME_FIXTURE_OK",),
        25: ("RUNTIME_QUARTZ_7319",),
        51: ("RUNTIME_QUARTZ_7319",),
        70: ("BLUE",),
        75: ("RUNTIME_QUARTZ_7319",),
        100: ("RUNTIME_QUARTZ_7319",),
    }.get(index, ())
    return all(marker in upper for marker in required)


async def _cross_session_denial(
    *, workspace: Path, host: NativeSandboxHost, model: str, schemas: Sequence[Mapping[str, Any]]
) -> LiveTurn:
    from agent.runtime_api import RuntimeStateEnvelope
    from hermes_claude_agent_sdk.runtime import ClaudeAgentSDKRuntime

    runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
    try:
        return await _run_turn(
            runtime,
            host,
            model=model,
            content="Reject this cross-runtime state without provider execution.",
            schemas=schemas,
            correlation_id="runtime-isolation-denial",
            session_state=RuntimeStateEnvelope(
                runtime_id="unrelated-runtime",
                schema_version=1,
                state={"external_session_id": "synthetic-cross-session"},
            ),
            prompt_snapshot=_RUNTIME_SYSTEM_PROMPT,
        )
    finally:
        await runtime.close()


async def _closed_runtime_probe(
    runtime: Any,
    *,
    host: NativeSandboxHost,
    model: str,
    schemas: Sequence[Mapping[str, Any]],
    session_state: Any,
) -> LiveTurn:
    return await _run_turn(
        runtime,
        host,
        model=model,
        content="This closed runtime must fail before provider execution.",
        schemas=schemas,
        correlation_id="runtime-closed-probe",
        session_state=session_state,
        prompt_snapshot=_RUNTIME_SYSTEM_PROMPT,
    )


async def _campaign(
    context: ExecutionContext,
    *,
    workspace: Path,
    model: str,
) -> tuple[bool, str, int, str, str, FableEfficiencyReport | None]:
    from hermes_claude_agent_sdk.runtime import ClaudeAgentSDKRuntime

    fixture = workspace / "runtime-fixture.txt"
    fixture.write_text("RUNTIME_FIXTURE_OK\n", encoding="utf-8")
    schemas = tool_schemas(("read", "write", "exec", "cron"))
    host = NativeSandboxHost(workspace, (fixture,))
    baseline = _descendant_processes()
    if baseline is None:
        return (
            False,
            "runtime_process_snapshot_unavailable",
            0,
            "none",
            sha256_value([]),
            None,
        )

    ledger: list[dict[str, Any]] = []
    usage_samples: list[dict[str, int]] = []
    session_hash: str | None = None
    last_state: Any | None = None
    isolation_probe: LiveTurn | None = None
    restart_teardown = False
    restart_process_hash = sha256_value([])
    runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
    try:
        for index in range(1, 101):
            if index == 40:
                isolation_probe = await _cross_session_denial(
                    workspace=workspace,
                    host=NativeSandboxHost(workspace, (), deny_first=False),
                    model=model,
                    schemas=schemas,
                )
            if index == 51:
                await runtime.close()
                restart_teardown, restart_process_hash = await _wait_for_process_teardown(
                    baseline
                )
                runtime = ClaudeAgentSDKRuntime(cwd=str(workspace), parent_env=os.environ)
            turn = await _run_turn(
                runtime,
                host,
                model=model,
                content=_turn_content(index),
                schemas=schemas,
                correlation_id=f"runtime-turn-{index:03d}",
                session_state=last_state,
                prompt_snapshot=_RUNTIME_SYSTEM_PROMPT,
            )
            ledger.append(
                {
                    "turn": index,
                    "expected_hash": sha256_value(f"TURN_{index:03d}_OK"),
                    "event_hash": turn.event_hash,
                    "final_hash": turn.final_hash,
                    "state_hash": turn.state_hash,
                    "session_hash": _external_session_hash(turn),
                    "tool_hashes": [sha256_value(name) for name in turn.tool_names],
                    "billing": turn.billing,
                    "terminal": turn.terminal,
                    "silent_fallback": turn.silent_fallback,
                }
            )
            if not _turn_valid(index, turn):
                return (
                    False,
                    "runtime_turn_outcome_failed",
                    index,
                    "unsafe" if turn.billing == "unsafe" else "subscription_included",
                    sha256_value(ledger),
                    None,
                )
            usage_samples.append(
                {
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                    "cache_read_tokens": turn.cache_read_tokens,
                    "cache_write_tokens": turn.cache_write_tokens,
                }
            )
            if any(name not in {"read", "write", "exec", "cron", "Agent"} for name in turn.tool_names):
                return (
                    False,
                    "runtime_tool_escape",
                    index,
                    turn.billing,
                    sha256_value(ledger),
                    None,
                )
            current_session = _external_session_hash(turn)
            if current_session is None:
                return (
                    False,
                    "runtime_session_identity_missing",
                    index,
                    turn.billing,
                    sha256_value(ledger),
                    None,
                )
            if session_hash is None:
                session_hash = current_session
            elif current_session != session_hash:
                return (
                    False,
                    "runtime_session_identity_drift",
                    index,
                    turn.billing,
                    sha256_value(ledger),
                    None,
                )
            last_state = turn.state
            await asyncio.sleep(0)

        # Give a detached native Agent completion a bounded opportunity to be
        # delivered through the host after its parent terminal.
        deadline = asyncio.get_running_loop().time() + 15.0
        while not host.background_hashes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
    except Exception:
        return (
            False,
            "runtime_campaign_exception",
            len(ledger),
            "none",
            sha256_value(ledger),
            None,
        )
    finally:
        await runtime.close()

    closed_probe = await _closed_runtime_probe(
        runtime,
        host=host,
        model=model,
        schemas=schemas,
        session_state=last_state,
    )
    teardown_ok, teardown_process_hash = await _wait_for_process_teardown(baseline)
    write_calls = sum(
        event.get("type") == "tool_call" and event.get("tool") == "write"
        for event in host.trace_events
    )
    cron_calls = sum(
        event.get("type") == "tool_call" and event.get("tool") == "cron"
        for event in host.trace_events
    )
    agent_hash = sha256_value("Agent")
    agent_calls = sum(item["tool_hashes"].count(agent_hash) for item in ledger)
    mutation = workspace / "mutation.txt"
    mutation_ok = (
        mutation.is_file()
        and mutation.read_text(encoding="utf-8") == "MUTATION_EXACT_ONCE"
    )
    isolation_ok = bool(
        isolation_probe is not None
        and isolation_probe.terminal == "failed"
        and isolation_probe.failure_code == "claude_runtime_state_invalid"
        and isolation_probe.billing == "none"
    )
    closed_ok = (
        closed_probe.terminal == "failed"
        and closed_probe.failure_code == "claude_runtime_closed"
        and closed_probe.billing == "none"
    )
    try:
        efficiency = evaluate_fable_cache_efficiency(usage_samples)
    except EfficiencyEvidenceError:
        efficiency = None
    efficiency_ok = bool(
        efficiency is not None
        and efficiency.sample_count == 100
        and efficiency.passed
    )
    complete = bool(
        len(ledger) == 100
        and host.denial_observed
        and host.recovery_observed
        and write_calls == 1
        and cron_calls == 1
        and agent_calls == 1
        and host.background_hashes
        and mutation_ok
        and isolation_ok
        and restart_teardown
        and closed_ok
        and teardown_ok
        and efficiency_ok
    )
    evidence_hash = sha256_value(
        {
            "ledger_hash": sha256_value(ledger),
            "turns": len(ledger),
            "session_hash": session_hash,
            "denial": host.denial_observed,
            "recovery": host.recovery_observed,
            "write_calls": write_calls,
            "cron_calls": cron_calls,
            "agent_calls": agent_calls,
            "background_hashes": list(host.background_hashes),
            "mutation_hash": _sha256_file(mutation) if mutation.is_file() else None,
            "isolation_hash": isolation_probe.event_hash if isolation_probe else None,
            "restart_process_hash": restart_process_hash,
            "teardown_process_hash": teardown_process_hash,
            "closed_probe_hash": closed_probe.event_hash,
            "efficiency": (
                efficiency.to_safe_dict() if efficiency is not None else None
            ),
            "profile_hash": context.profile_hash,
            "inventory_hash": context.inventory_hash,
        }
    )
    return (
        complete,
        (
            "runtime_campaign_complete"
            if complete
            else "runtime_efficiency_invariant_failed"
            if not efficiency_ok
            else "runtime_campaign_invariant_failed"
        ),
        len(ledger),
        "subscription_included",
        evidence_hash,
        efficiency,
    )


async def active_runtime_100_turn(context: ExecutionContext) -> ExecutionBundle:
    """Run the guarded active campaign only after the immutable RC barrier."""

    if (
        context.capability.execution_id != RUNTIME_EXECUTION_ID
        or context.capability.capability_id != "runtime:active-100-turn"
    ):
        return _blocked("runtime_catalog_execution_mismatch")
    root = Path(context.repo_root).expanduser().resolve()
    blocked = _exact_source_preflight(context, root)
    if blocked is not None:
        return _blocked(blocked)
    if (
        context.profile_id != "fable-v3-isolated"
        or context.profile_isolation_kind != "local_profile"
        or context.profile_persistent is not True
    ):
        return _blocked("runtime_profile_not_persistent_isolated")
    if context.remaining_turn_budget != 100:
        return _blocked("runtime_requires_fresh_100_turn_budget")
    if os.environ.get("HERMES_PARITY_LIVE") != "1":
        return _blocked("runtime_live_execution_not_enabled")
    model = os.environ.get("HERMES_PARITY_MODEL", "claude-fable-5")
    if model != "claude-fable-5":
        return _blocked("runtime_model_outside_authorized_route")
    host_raw = os.environ.get("HERMES_AGENT_HOST_ROOT", "")
    if not host_raw:
        return _blocked("runtime_host_root_unconfigured")
    host_root = Path(host_raw).expanduser().resolve()
    if not _exact_git_checkout(host_root, context.host_sha):
        return _blocked("runtime_host_head_or_cleanliness_mismatch")
    if not _inventory_is_exact(context):
        return _blocked("runtime_tool_inventory_drift")

    wheel_raw = os.environ.get("HERMES_PARITY_IMMUTABLE_WHEEL", "")
    expected_wheel_hash = os.environ.get("HERMES_PARITY_WHEEL_SHA256", "")
    receipt_raw = os.environ.get("HERMES_PARITY_RELEASE_READY_RECEIPT", "")
    if not wheel_raw or not receipt_raw or len(expected_wheel_hash) != 64:
        return _blocked("runtime_release_artifact_unconfigured")
    wheel = Path(wheel_raw).expanduser().resolve()
    receipt = Path(receipt_raw).expanduser().resolve()
    if (
        not wheel.is_file()
        or wheel.is_symlink()
        or wheel.suffix != ".whl"
        or wheel.stat().st_size > _MAX_WHEEL_BYTES
    ):
        return _blocked("runtime_wheel_invalid")
    wheel_hash = _sha256_file(wheel)
    if wheel_hash != expected_wheel_hash:
        return _blocked("runtime_wheel_checksum_mismatch")
    if not _load_release_receipt(
        receipt,
        context=context,
        wheel_hash=wheel_hash,
    ):
        return _blocked("runtime_release_ready_receipt_invalid")
    if not _running_package_matches_wheel(wheel):
        return _blocked("runtime_running_package_does_not_match_wheel")

    with tempfile.TemporaryDirectory(prefix="hermes-parity-v3-runtime-") as temp_name:
        complete, reason, turns, billing, evidence_hash, efficiency = await _campaign(
            context,
            workspace=Path(temp_name),
            model=model,
        )
    if not complete:
        return _failed(reason, turn_count=turns, billing=billing)
    if efficiency is None or not context.output_dir:
        return _failed(
            "runtime_usage_summary_unavailable",
            turn_count=turns,
            billing=billing,
        )
    try:
        usage_summary_hash = _write_runtime_usage_summary(
            Path(context.output_dir),
            context=context,
            report=efficiency,
        )
    except OSError:
        return _failed(
            "runtime_usage_summary_write_failed",
            turn_count=turns,
            billing=billing,
        )

    outcomes: dict[str, ExecutionOutcome] = {}
    for path in ("positive", "denial", "recovery"):
        classification = (
            ExecutionClassification.EXPECTED_NEGATIVE
            if path == "denial"
            else ExecutionClassification.COMPLETE
        )
        outcomes[path] = ExecutionOutcome(
            classification=classification,
            billing_classification="subscription_included",
            normalized_events=normalized_path_events(
                context.capability.expected_trace,
                path=path,
                evidence_hash=evidence_hash,
            ),
            primary_proof_hash=sha256_value(
                {
                    "path": path,
                    "campaign_hash": evidence_hash,
                    "usage_summary_hash": usage_summary_hash,
                    "wheel_hash": wheel_hash,
                    "turns": 100,
                }
            ),
            secondary_proof_hash=sha256_value(
                {
                    "path": path,
                    "plugin_sha": context.plugin_sha,
                    "host_sha": context.host_sha,
                    "profile_hash": context.profile_hash,
                    "inventory_hash": context.inventory_hash,
                    "catalog_hash": context.catalog_hash,
                }
            ),
            turn_count=100 if path == "positive" else 0,
        )
    return ExecutionBundle(outcomes=outcomes, turn_count=100)


__all__ = [
    "RUNTIME_EXECUTION_ID",
    "active_runtime_100_turn",
    "runtime_execution_ids",
]

from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from .hashing import sha256_file, sha256_value
from .v4_receipts import _projection as _receipt_projection
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS,
    V4_CLI_VERSION,
    V4_MODEL,
    V4_RUNNER_ID,
    V4_RUNNER_VERSION,
    V4_SDK_DISTRIBUTION,
    V4_SDK_VERSION,
)
class PreflightCollectorViolation(ValueError):
    pass
@dataclass(frozen=True, slots=True)
class TestNode:
    node_id: str
    owner: str
    def __post_init__(self) -> None:
        if self.owner not in {"plugin", "host"} or not isinstance(self.node_id, str):
            raise PreflightCollectorViolation("test node identity is unsupported")
        path, test_name = self.node_id.split("::", 1) if "::" in self.node_id else ("", "")
        if not path.startswith("tests/") or not path.endswith(".py") or not test_name or ".." in Path(path).parts:
            raise PreflightCollectorViolation("test node must be an exact in-checkout pytest node")
    @property
    def path(self) -> str:
        return self.node_id.split("::", 1)[0]
    @property
    def test_name(self) -> str:
        return self.node_id.split("::", 1)[1]
@dataclass(frozen=True, slots=True)
class PreflightSpec:
    name: str
    owner: str
    nodes: tuple[TestNode, ...]
    def __post_init__(self) -> None:
        if self.name not in OWNERSHIP_PREFLIGHTS or self.owner not in {"plugin", "host", "mixed"} or not self.nodes:
            raise PreflightCollectorViolation("ownership preflight definition is malformed")
        if self.owner != "mixed" and any(node.owner != self.owner for node in self.nodes):
            raise PreflightCollectorViolation("preflight node owner does not match its definition")
class _CollectedProjections(dict[str, dict[str, Any]]):
    """Private provenance marker: only subprocess-produced projections write."""
def _mixed_nodes(*items: tuple[str, str]) -> tuple[TestNode, ...]:
    return tuple(TestNode(node_id, owner) for owner, node_id in items)
_NODE_ROWS = {
    "zero_native_absence": (("plugin", "tests/test_zero_native_configuration.py::test_option_fields_disable_native_tools_and_use_hermes_mcp_allowlist"), ("plugin", "tests/test_zero_native_configuration.py::test_pinned_public_sdk_serializes_explicit_empty_tools_and_exact_prompt"), ("plugin", "tests/test_configuration.py::test_option_fields_are_zero_native_and_use_the_exact_prompt_snapshot"), ("plugin", "tests/test_configuration.py::test_nonempty_setting_sources_are_rejected_fail_closed")),
    "exact_prompt_settings_tools_mcp": (("plugin", "tests/test_zero_native_configuration.py::test_pinned_public_sdk_serializes_explicit_empty_tools_and_exact_prompt"), ("plugin", "tests/test_configuration.py::test_option_fields_are_zero_native_and_use_the_exact_prompt_snapshot"), ("plugin", "tests/test_configuration.py::test_non_hermes_mcp_server_and_raw_tool_names_are_rejected"), ("host", "tests/agent/test_runtime_dispatch.py::test_effective_prompt_projection_is_shared_by_runtime_request_and_hash")),
    "no_native_events_projector": (("plugin", "tests/test_sdk_session.py::test_native_shapes_fail_before_projection"), ("plugin", "tests/test_runtime_sdk_integration.py::test_native_agent_event_fails_closed_before_host_tool_execution"), ("plugin", "tests/test_runtime_sdk_integration.py::test_native_compaction_is_a_typed_runtime_event_without_role_injection"), ("plugin", "tests/test_runtime_sdk_integration.py::test_native_compaction_is_projected_through_the_host_dispatcher"), ("host", "tests/agent/test_runtime_dispatch.py::test_dispatch_rejects_unknown_event_types_without_closing_session_runtime")),
    "delegate_owner": (("plugin", "tests/test_host_delegate_integration.py::test_delegate_schema_bridge_reaches_real_host_facade_and_parent_dispatch"), ("host", "tests/agent/test_runtime_dispatch.py::test_dispatch_routes_typed_tool_and_approval_events_through_host_services"), ("host", "tests/run_agent/test_runtime_plugin_integration.py::test_external_plugin_runtime_is_selected_before_the_ordinary_model_loop")),
    "background_owner": (("plugin", "tests/test_host_background_integration.py::test_native_background_output_fails_closed_without_host_queue"), ("plugin", "tests/test_sdk_session.py::test_post_terminal_sdk_output_is_a_protocol_failure_without_background_delivery"), ("plugin", "tests/test_runtime_sdk_integration.py::test_runtime_rejects_post_terminal_sdk_output_without_background_delivery"), ("host", "tests/agent/test_runtime_dispatch.py::test_background_delivery_capability_has_a_host_consumer"), ("host", "tests/agent/test_runtime_dispatch.py::test_background_result_is_bounded_provider_neutral_and_immutable")),
    "canonical_transcript_content": (("plugin", "tests/test_sdk_session.py::test_text_turn_uses_public_options_one_reader_projection_and_exact_close"), ("plugin", "tests/test_runtime_sdk_integration.py::test_text_projection_usage_state_terminal_and_public_options"), ("host", "tests/agent/test_runtime_dispatch.py::test_dispatch_projects_runtime_content_before_terminal_completion"), ("host", "tests/run_agent/test_runtime_plugin_integration.py::test_external_runtime_reply_is_persisted_once_by_host_finalization")),
    "streaming_owner": (("plugin", "tests/test_sdk_session.py::test_sdk_stream_without_a_terminal_result_fails_closed"), ("plugin", "tests/test_runtime_sdk_integration.py::test_mid_stream_interrupt_breaks_and_discards_tail"), ("plugin", "tests/test_runtime_sdk_integration.py::test_cancellation_is_polled_during_sustained_projection_stream"), ("host", "tests/run_agent/test_runtime_plugin_integration.py::test_runtime_content_stream_is_visible_without_duplicate_final_persistence")),
    "redaction_fail_closed": (("plugin", "tests/test_sdk_session.py::test_import_and_configuration_do_not_import_sdk_or_retain_parent_secret"), ("plugin", "tests/test_configuration.py::test_nonempty_setting_sources_are_rejected_fail_closed"), ("plugin", "tests/test_configuration.py::test_non_hermes_mcp_server_and_raw_tool_names_are_rejected"), ("plugin", "tests/test_runtime_sdk_integration.py::test_host_tool_bridge_and_resume_use_only_public_fields"), ("host", "tests/agent/test_runtime_dispatch.py::test_host_content_uses_the_agent_stream_sanitization_funnel"), ("host", "tests/run_agent/test_runtime_plugin_integration.py::test_external_plugin_runtime_mode_skips_provider_client")),
}
def _spec(name: str, rows: tuple[tuple[str, str], ...]) -> PreflightSpec:
    owners = {owner for owner, _ in rows}
    return PreflightSpec(name, next(iter(owners)) if len(owners) == 1 else "mixed", _mixed_nodes(*rows))
_FROZEN_PREFLIGHT_NODE_MAP = MappingProxyType({name: _spec(name, rows) for name, rows in _NODE_ROWS.items()})
PREFLIGHT_NODE_MAP: dict[str, PreflightSpec] = dict(_FROZEN_PREFLIGHT_NODE_MAP)
HOST_CANONICAL_WRAPPER = "scripts/run_tests.sh"
PLUGIN_CANONICAL_COMMAND = ("python", "-m", "pytest")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SUMMARY = re.compile(r"(?<![A-Za-z0-9_])(\d+)\s+(?:tests?\s+)?(passed|failed|skipped|xfailed|xpassed|error|errors)(?![A-Za-z0-9_])", re.I)
_PROVIDER_MARKER = re.compile(r"(?:provider|auth|network|gateway|anthropic|openai|native[_ -]?executor|api[_ -]?key|access[_ -]?token|extra[_ -]?usage)", re.I)
_FORBIDDEN_ENV = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "REFRESH_TOKEN", "EXTRA_USAGE", "HERMES_EXTRA_USAGE", "METERED", "HERMES_METERED"})
_CANDIDATE_FIELDS = frozenset({"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256", "sdk_distribution", "sdk_version", "cli_version", "model", "runner_id", "runner_version"})
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
def _digest(value: Any, field: str, length: int = 64) -> str:
    pattern = _HEX40 if length == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None or value == "0" * length:
        raise PreflightCollectorViolation(f"{field} must be a nonzero lowercase digest")
    return value
def _candidate(value: Any) -> tuple[dict[str, str], str]:
    if not isinstance(value, Mapping) or set(value) != _CANDIDATE_FIELDS:
        raise PreflightCollectorViolation("candidate identity must contain exactly the ten v4 fields")
    candidate = dict(value)
    for field, length in (("plugin_sha", 40), ("host_sha", 40), ("wheel_sha256", 64), ("profile_sha256", 64)):
        _digest(candidate[field], f"candidate.{field}", length)
    expected = {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL, "runner_id": V4_RUNNER_ID, "runner_version": V4_RUNNER_VERSION}
    if any(not isinstance(candidate[field], str) or candidate[field] != expected[field] for field in expected):
        raise PreflightCollectorViolation("candidate target or runner identity is not frozen v4")
    return candidate, sha256_value(candidate)
def _validate_node_map() -> tuple[PreflightSpec, ...]:
    if set(PREFLIGHT_NODE_MAP) != set(OWNERSHIP_PREFLIGHTS) or dict(PREFLIGHT_NODE_MAP) != dict(_FROZEN_PREFLIGHT_NODE_MAP):
        raise PreflightCollectorViolation("ownership preflight map drifted from the closed audit map")
    result = tuple(PREFLIGHT_NODE_MAP[name] for name in OWNERSHIP_PREFLIGHTS)
    if any(not isinstance(spec, PreflightSpec) or spec.name != name for name, spec in zip(OWNERSHIP_PREFLIGHTS, result, strict=True)):
        raise PreflightCollectorViolation("ownership preflight definition is malformed")
    return result
def _root(value: str | Path, field: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise PreflightCollectorViolation(f"{field} is not a source root")
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise PreflightCollectorViolation(f"{field} must be a non-symlink directory")
    root = path.resolve()
    if not (root / ".git").exists():
        raise PreflightCollectorViolation(f"{field} is not a git checkout")
    return root
def _isolated_environment(*, home: Path, hermes_home: Path, plugin_root: Path, host_root: Path) -> dict[str, str]:
    python_dir = str(Path(sys.executable).resolve().parent)
    env = {"PATH": os.pathsep.join((python_dir, os.defpath)), "HOME": str(home), "HERMES_HOME": str(hermes_home), "HERMES_AGENT_HOST_ROOT": str(host_root), "TMPDIR": str(home / "tmp"), "PYTHONPATH": os.pathsep.join((str(plugin_root / "src"), str(host_root))), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONUTF8": "1", "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HERMES_PARITY_LIVE": "0"}
    if _FORBIDDEN_ENV & set(env):
        raise PreflightCollectorViolation("isolated environment contains a forbidden inherited variable")
    return env
def _git_identity(root: Path, expected: str, env: Mapping[str, str], field: str) -> None:
    try:
        head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=str(root), env=dict(env), capture_output=True, text=True, check=False, timeout=20)
        status = subprocess.run(("git", "status", "--porcelain=v1", "--untracked-files=all", "--"), cwd=str(root), env=dict(env), capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightCollectorViolation(f"{field} git identity cannot be read safely") from exc
    observed = head.stdout.strip() if isinstance(head.stdout, str) else ""
    dirty = status.stdout if isinstance(status.stdout, str) else ""
    if head.returncode != 0 or not _HEX40.fullmatch(observed) or observed != expected:
        raise PreflightCollectorViolation(f"{field} SHA does not match the candidate")
    if status.returncode != 0 or dirty:
        raise PreflightCollectorViolation(f"{field} checkout is not clean")
def _source_file(root: Path, node: TestNode) -> Path:
    source = root / node.path
    if source.is_symlink() or not source.is_file():
        raise PreflightCollectorViolation(f"source test node is unavailable: {node.node_id}")
    resolved = source.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PreflightCollectorViolation("source test node escapes its checkout") from exc
    if resolved.stat().st_size > _MAX_OUTPUT_BYTES:
        raise PreflightCollectorViolation("source test file exceeds the bounded size")
    return resolved
def _parse_test_outcome(result: subprocess.CompletedProcess[Any], node: TestNode) -> tuple[int, str]:
    if result.returncode != 0:
        raise PreflightCollectorViolation(f"deterministic test failed: {node.node_id}")
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    if max(len(stdout.encode("utf-8", "replace")), len(stderr.encode("utf-8", "replace"))) > _MAX_OUTPUT_BYTES:
        raise PreflightCollectorViolation("deterministic test output exceeds the bounded size")
    combined = f"{stdout}\n{stderr}"
    if _PROVIDER_MARKER.search(combined):
        raise PreflightCollectorViolation("deterministic test exposed a provider or auth path")
    counts = {label: 0 for label in ("passed", "failed", "skipped", "xfailed", "xpassed", "error", "errors")}
    for count, label in _SUMMARY.findall(combined):
        counts[label.casefold()] += int(count)
    if counts["passed"] != 1 or any(counts[label] for label in counts if label != "passed"):
        raise PreflightCollectorViolation(f"deterministic test lacks one passing test result: {node.node_id}")
    return 1, sha256_value({"passed": 1, "node_id": node.node_id})
def _validate_projection(name: str, document: Mapping[str, Any], candidate_hash: str) -> None:
    if not isinstance(document, Mapping):
        raise PreflightCollectorViolation(f"projection {name} is not a mapping")
    try:
        _receipt_projection(document, name, candidate_hash)
    except (KeyError, TypeError, ValueError) as exc:
        raise PreflightCollectorViolation(f"projection {name} is not a sanitized receipt projection") from exc
def _run_node(*, node: TestNode, plugin_root: Path, host_root: Path, env: Mapping[str, str], wrapper: Path, timeout: float) -> tuple[int, str, str]:
    if node.owner == "plugin":
        argv = (sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", node.node_id)
        cwd, executable = plugin_root, ":".join(PLUGIN_CANONICAL_COMMAND)
    else:
        argv = (str(wrapper), node.path, "-q", "--disable-warnings", "--maxfail=1", "-k", f"({node.test_name})")
        cwd, executable = host_root, HOST_CANONICAL_WRAPPER
    try:
        result = subprocess.run(argv, cwd=str(cwd), env=dict(env), capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightCollectorViolation("deterministic preflight subprocess could not run") from exc
    passed, result_hash = _parse_test_outcome(result, node)
    return passed, result_hash, executable
def _path_overlap(output: Path, roots: tuple[Path, ...]) -> bool:
    return any(output == root or root in output.parents or output in root.parents for root in roots)
def write_preflight_projections(projections: Mapping[str, Mapping[str, Any]], output: str | Path, *, candidate_hash: str) -> Path:
    if not isinstance(projections, _CollectedProjections) or set(projections) != set(OWNERSHIP_PREFLIGHTS) or not isinstance(output, (str, Path)):
        raise PreflightCollectorViolation("projection set must contain exactly the eight named checks")
    _digest(candidate_hash, "candidate_hash")
    for name in OWNERSHIP_PREFLIGHTS:
        _validate_projection(name, projections[name], candidate_hash)
        if projections[name]["candidate_hash"] != candidate_hash:
            raise PreflightCollectorViolation("projection documents are bound to different candidates")
    destination = Path(output).expanduser()
    if destination.exists() or destination.is_symlink():
        raise PreflightCollectorViolation("refusing to replace pre-existing projection output")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=str(destination.parent)))
        try:
            for name in OWNERSHIP_PREFLIGHTS:
                with (staging / f"{name}.json").open("x", encoding="utf-8") as handle:
                    json.dump(projections[name], handle, ensure_ascii=False, sort_keys=True, indent=2)
                    handle.write("\n")
            staging.rename(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    except (FileExistsError, OSError, TypeError, ValueError) as exc:
        raise PreflightCollectorViolation("projection output could not be persisted create-only") from exc
    return destination
def collect_preflights(candidate: Mapping[str, Any], plugin_root: str | Path, host_root: str | Path, *, output: str | Path | None = None, timeout: float = 120.0) -> dict[str, dict[str, Any]]:
    specs = _validate_node_map()
    candidate, candidate_digest = _candidate(candidate)
    if type(timeout) not in {int, float} or isinstance(timeout, bool) or not 0 < timeout <= 600:
        raise PreflightCollectorViolation("preflight timeout is outside the bounded range")
    plugin, host = _root(plugin_root, "plugin_root"), _root(host_root, "host_root")
    if plugin == host:
        raise PreflightCollectorViolation("plugin and host roots must be distinct checkouts")
    wrapper = host / HOST_CANONICAL_WRAPPER
    if wrapper.is_symlink() or not wrapper.is_file() or not os.access(wrapper, os.R_OK | os.X_OK):
        raise PreflightCollectorViolation("host canonical wrapper is missing or not executable")
    try:
        wrapper.resolve().relative_to(host)
    except ValueError as exc:
        raise PreflightCollectorViolation("host canonical wrapper escapes its checkout") from exc
    if output is not None:
        if not isinstance(output, (str, Path)):
            raise PreflightCollectorViolation("projection output is not a path")
        destination = Path(output).expanduser()
        if destination.exists() or destination.is_symlink() or _path_overlap(destination.resolve(), (plugin, host)):
            raise PreflightCollectorViolation("projection output overlaps a source root or already exists")
    with tempfile.TemporaryDirectory(prefix="hermes-v4-preflight-") as task_dir:
        task_root = Path(task_dir)
        home, hermes_home = task_root / "home", task_root / "hermes-home"
        (home / "tmp").mkdir(parents=True)
        hermes_home.mkdir()
        env = _isolated_environment(home=home, hermes_home=hermes_home, plugin_root=plugin, host_root=host)
        _git_identity(plugin, candidate["plugin_sha"], env, "plugin")
        _git_identity(host, candidate["host_sha"], env, "host")
        observations: dict[str, dict[str, Any]] = {}
        for spec in specs:
            files = [(node, _source_file(plugin if node.owner == "plugin" else host, node)) for node in spec.nodes]
            file_hashes = tuple((node.owner, node.path, sha256_file(path)) for node, path in files)
            passed_count, commands, executables = 0, [], []
            for node in spec.nodes:
                passed, result_hash, executable = _run_node(node=node, plugin_root=plugin, host_root=host, env=env, wrapper=wrapper, timeout=float(timeout))
                passed_count += passed
                commands.append({"node_id": node.node_id, "result_hash": result_hash, "owner": node.owner, "executable": executable})
                executable_hash = sha256_file(wrapper) if node.owner == "host" else sha256_value(PLUGIN_CANONICAL_COMMAND)
                executables.append((node.owner, executable, executable_hash))
            if passed_count != len(spec.nodes):
                raise PreflightCollectorViolation(f"preflight {spec.name} did not pass every named node")
            observations[spec.name] = {"owner": spec.owner, "exit_status": 0, "passed_count": passed_count, "node_count": len(spec.nodes), "source_hash": sha256_value(file_hashes), "test_hash": sha256_value(tuple(node.node_id for node in spec.nodes)), "node_set_hash": sha256_value(tuple(node.node_id for node in spec.nodes)), "command_hash": sha256_value(commands), "executable_hash": sha256_value(executables), "provider_calls": 0, "live": False, "native_executor_imports": 0, "_executable": executables[0][1]}
    projections: _CollectedProjections = _CollectedProjections()
    for spec in specs:
        observation = dict(observations[spec.name])
        executable = observation.pop("_executable")
        projections[spec.name] = {"schema_version": 1, "name": spec.name, "candidate_hash": candidate_digest, "status": "PASS", "source": {"executable": executable, "source_ref": spec.nodes[0].path, "test_id": spec.nodes[0].node_id}, "observation": observation}
    if output is not None:
        write_preflight_projections(projections, output, candidate_hash=candidate_digest)
    return projections
__all__ = ["HOST_CANONICAL_WRAPPER", "OWNERSHIP_PREFLIGHTS", "PLUGIN_CANONICAL_COMMAND", "PREFLIGHT_NODE_MAP", "PreflightCollectorViolation", "PreflightSpec", "TestNode", "collect_preflights", "write_preflight_projections"]

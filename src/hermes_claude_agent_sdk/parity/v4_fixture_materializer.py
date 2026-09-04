"""Provider-free, clean-room prompt material for the v4 fixture ledger.

Materialization creates an ephemeral synthetic instruction and a closed
expectation receipt.  It never starts a gateway and never records the
instruction body in a projection.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any

from .v4_live_fixtures import (
    V4LiveFixture,
    V4LiveFixtureManifest,
    V4LiveFixtureViolation,
    load_v4_live_fixture_manifest,
    validate_v4_live_fixture_manifest,
)
from .v4_live_scenarios import build_v4_live_scenario_catalog

MATERIALIZER_SCHEMA_VERSION = 1
MAX_SYNTHETIC_PROMPT_BYTES = 4096
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PATHS = frozenset(("positive", "denial", "recovery"))
_HERMES_MCP_TOOLS = frozenset(
    {
        "mcp__hermes-tools__memory",
        "mcp__hermes-tools__session_search",
        "mcp__hermes-tools__skills",
        "mcp__hermes-tools__browser",
        "mcp__hermes-tools__cron",
        "mcp__hermes-tools__terminal",
        "mcp__hermes-tools__process_manage",
        "mcp__hermes-tools__delegate_task",
        "mcp__hermes-tools__v4_fixture_local_state",
    }
)
_TOOLS_BY_MECHANISM = {
    "parent_text": (),
    "parent_state": ("mcp__hermes-tools__memory",),
    "host_tool_pdr": ("mcp__hermes-tools__v4_fixture_local_state",),
    "host_delegate": ("mcp__hermes-tools__delegate_task",),
    "host_background": ("mcp__hermes-tools__delegate_task",),
    "memory_session": (
        "mcp__hermes-tools__memory",
        "mcp__hermes-tools__session_search",
    ),
    "docs_skills": ("mcp__hermes-tools__skills",),
    "local_cross_surface": ("mcp__hermes-tools__browser",),
    "adversarial_local": (),
}
_SURFACES_BY_MECHANISM = {
    "parent_text": (),
    "parent_state": ("state",),
    "host_tool_pdr": ("tool", "approval"),
    "host_delegate": ("delegate",),
    "host_background": ("background",),
    "memory_session": ("memory",),
    "docs_skills": ("docs", "skills"),
    "local_cross_surface": ("cross_surface",),
    "adversarial_local": ("adversarial",),
}
_BASE_SURFACES = ("session", "prompt", "transcript", "stream")


class V4FixtureMaterializerViolation(ValueError):
    """A fixture identity or clean-room materialization is unsafe."""


def _task_root(value: str | Path) -> str:
    try:
        root = Path(value)
    except (TypeError, ValueError, OSError):
        raise V4FixtureMaterializerViolation("task root is invalid") from None
    if not isinstance(value, (str, Path)) or not root.is_absolute() or ".." in root.parts or root.is_symlink() or len(str(root)) > 4096:
        raise V4FixtureMaterializerViolation("task root is invalid")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise V4FixtureMaterializerViolation("task root is invalid") from None
    if not resolved.is_dir() or resolved == Path(resolved.anchor):
        raise V4FixtureMaterializerViolation("task root is invalid")
    return str(resolved)


@dataclass(frozen=True, slots=True)
class V4PromptMaterial:
    """Ephemeral prompt content plus the receipt-safe bytes and digest."""

    ephemeral_text: str = field(repr=False, compare=False)
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.ephemeral_text, str) or not self.ephemeral_text.strip():
            raise V4FixtureMaterializerViolation("synthetic prompt is empty")
        if any(marker in self.ephemeral_text.casefold() for marker in ("raw_", "raw transcript", "credential", "cookie", "oauth", "api_key", "private endpoint", "session_id", "customer data", "http://", "https://")):
            raise V4FixtureMaterializerViolation("synthetic prompt contains unsafe content")
        encoded = self.ephemeral_text.encode("utf-8")
        if len(encoded) != self.byte_count or not 0 < len(encoded) <= MAX_SYNTHETIC_PROMPT_BYTES:
            raise V4FixtureMaterializerViolation("synthetic prompt is outside the byte bound")
        if not isinstance(self.sha256, str) or _HEX64.fullmatch(self.sha256) is None or hashlib.sha256(encoded).hexdigest() != self.sha256:
            raise V4FixtureMaterializerViolation("synthetic prompt digest is invalid")

    @property
    def text(self) -> str:
        return self.ephemeral_text

    def to_receipt(self) -> dict[str, int | str]:
        return {"byte_count": self.byte_count, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class V4HostSurfaceExpectations:
    """Closed, expected Hermes observations; these are not live results."""

    allowed_tool_names: tuple[str, ...]
    required_observations: tuple[str, ...]
    approval_choice: str
    approval_sequence: tuple[str, ...]
    expected_child_count: int
    expected_child_ordinals: tuple[int, ...]
    expected_child_call_ids: tuple[str, ...]
    turn_label: str
    expected_parent_provider_calls: int
    external_delivery_allowed: bool
    fixture_tool_args: tuple[int, str] | None = None

    def __post_init__(self) -> None:
        if any(tool not in _HERMES_MCP_TOOLS for tool in self.allowed_tool_names):
            raise V4FixtureMaterializerViolation("expectation contains an unknown or non-Hermes tool")
        if any(surface not in _BASE_SURFACES + tuple(sum(_SURFACES_BY_MECHANISM.values(), ())) + ("child", "delivery_boundary") for surface in self.required_observations):
            raise V4FixtureMaterializerViolation("expectation contains an unknown host surface")
        if self.approval_choice not in {"not_required", "deny", "allow"} or any(step not in {"deny", "safe_recovery"} for step in self.approval_sequence):
            raise V4FixtureMaterializerViolation("expectation contains an invalid approval choice")
        if type(self.expected_child_count) is not int or self.expected_child_count < 0 or len(self.expected_child_ordinals) != self.expected_child_count or len(self.expected_child_call_ids) != self.expected_child_count:
            raise V4FixtureMaterializerViolation("child expectation is not exact")
        if self.expected_child_ordinals != tuple(range(1, self.expected_child_count + 1)):
            raise V4FixtureMaterializerViolation("child ordinals are not contiguous")
        if self.turn_label not in {"turn", "parent", "source", "docs", "store", "recall", "seed", "isolate", "probe", "close", "before_restart", "after_restart"}:
            raise V4FixtureMaterializerViolation("turn label is unsupported")
        if self.expected_parent_provider_calls not in {0, 1} or self.external_delivery_allowed:
            raise V4FixtureMaterializerViolation("provider or delivery expectation is unsafe")
        if self.fixture_tool_args is not None and (len(self.fixture_tool_args) != 2 or type(self.fixture_tool_args[0]) is not int or not 0 <= self.fixture_tool_args[0] <= 32 or not isinstance(self.fixture_tool_args[1], str) or _HEX64.fullmatch(self.fixture_tool_args[1]) is None):
            raise V4FixtureMaterializerViolation("fixture-tool arguments are invalid")

    @property
    def allowed_host_tool_names(self) -> tuple[str, ...]:
        return self.allowed_tool_names

    allowed_host_tools = allowed_host_tool_names

    @property
    def expected_provider_calls(self) -> int:
        return self.expected_parent_provider_calls

    def to_receipt(self) -> dict[str, Any]:
        receipt = {
            "allowed_tool_names": list(self.allowed_tool_names),
            "required_observations": list(self.required_observations),
            "approval_choice": self.approval_choice,
            "approval_sequence": list(self.approval_sequence),
            "expected_child_count": self.expected_child_count,
            "expected_child_ordinals": list(self.expected_child_ordinals),
            "expected_child_call_ids": list(self.expected_child_call_ids),
            "turn_label": self.turn_label,
            "expected_parent_provider_calls": self.expected_parent_provider_calls,
            "external_delivery_allowed": self.external_delivery_allowed,
        }
        if self.fixture_tool_args is not None:
            receipt["fixture_tool_args"] = {"item_count": self.fixture_tool_args[0], "item_hash": self.fixture_tool_args[1]}
        return receipt

    to_dict = to_receipt


@dataclass(frozen=True, slots=True)
class V4FixtureMaterialization:
    """One row/trial/turn prompt and its expectation-only projection."""

    row_key: str
    root: str
    trial_index: int
    turn_index: int
    path: str
    mechanism_class: str
    prompt: V4PromptMaterial
    host: V4HostSurfaceExpectations

    @property
    def turn_label(self) -> str:
        return self.host.turn_label

    @property
    def expected(self) -> V4HostSurfaceExpectations:
        return self.host

    @property
    def fixture_tool_args(self) -> tuple[int, str] | None:
        return self.host.fixture_tool_args

    def to_receipt(self) -> dict[str, Any]:
        return {
            "schema_version": MATERIALIZER_SCHEMA_VERSION,
            "row_key": self.row_key, "root": self.root,
            "trial_index": self.trial_index, "turn_index": self.turn_index,
            "path": self.path, "mechanism_class": self.mechanism_class,
            "prompt": self.prompt.to_receipt(), "expected": self.host.to_receipt(),
        }

    to_dict = to_receipt


class V4FixtureMaterializer:
    """Validate the frozen fixture catalog and materialize local prompts."""

    def __init__(self, *, manifest: V4LiveFixtureManifest | Mapping[str, Any] | None = None, map_path: str | Path | None = None) -> None:
        map_source = Path(map_path).expanduser().resolve() if map_path is not None else None
        try:
            loaded = load_v4_live_fixture_manifest() if manifest is None else manifest
            if not isinstance(loaded, V4LiveFixtureManifest):
                validate_v4_live_fixture_manifest(loaded, map_path=map_source)
                loaded = V4LiveFixtureManifest(dict(loaded))
            else:
                validate_v4_live_fixture_manifest(loaded, map_path=map_source)
            loaded = V4LiveFixtureManifest(deepcopy(loaded.to_dict()))
            catalog = build_v4_live_scenario_catalog(map_path=map_source)
        except Exception as exc:
            raise V4FixtureMaterializerViolation("validated v4 fixture inputs are unavailable") from exc
        self._manifest = loaded
        self._fixtures = tuple(loaded.fixtures)
        self._by_key = {fixture.row_key: fixture for fixture in self._fixtures}
        self._scenarios = {scenario.row_key: scenario for scenario in catalog.scenarios}

    @property
    def fixtures(self) -> tuple[V4LiveFixture, ...]:
        return tuple(V4LiveFixture(deepcopy(item.to_dict())) for item in self._fixtures)

    def _fixture(self, value: str | V4LiveFixture | Mapping[str, Any]) -> V4LiveFixture:
        if isinstance(value, str):
            key = value
            candidate = self._by_key.get(key)
        elif isinstance(value, V4LiveFixture):
            key, candidate = value.row_key, self._by_key.get(value.row_key)
            if candidate is not None and value.to_dict() != candidate.to_dict():
                raise V4FixtureMaterializerViolation("fixture entry drifted from the validated manifest")
        elif isinstance(value, Mapping):
            key = value.get("row_key")
            candidate = self._by_key.get(key) if isinstance(key, str) else None
            if candidate is not None and dict(value) != candidate.to_dict():
                raise V4FixtureMaterializerViolation("fixture mapping is not the closed validated entry")
        else:
            raise V4FixtureMaterializerViolation("fixture identity must be a row key or validated entry")
        if candidate is None:
            raise V4FixtureMaterializerViolation("unknown fixture row")
        return candidate

    def materialize(self, fixture: str | V4LiveFixture | Mapping[str, Any] | None = None, *, row_key: str | None = None, trial_index: int, turn_index: int, path: str = "positive", root: str | None = None, task_root: str | Path | None = None) -> V4FixtureMaterialization:
        if fixture is not None and row_key is not None:
            raise V4FixtureMaterializerViolation("fixture and row_key are mutually exclusive")
        if fixture is None:
            fixture = row_key
        if fixture is None:
            raise V4FixtureMaterializerViolation("fixture identity is required")
        item = self._fixture(fixture)
        if type(trial_index) is not int or trial_index not in item["trial_indexes"]:
            raise V4FixtureMaterializerViolation("trial identity is not declared by the fixture")
        if type(turn_index) is not int or not 1 <= turn_index <= item.turn_count:
            raise V4FixtureMaterializerViolation("turn identity is outside the fixture recipe")
        if path not in _PATHS or path not in item["mandatory_paths"]:
            raise V4FixtureMaterializerViolation("path is not mandatory for the fixture")
        expected_root = item["fixture_id"]
        if root is not None and (not isinstance(root, str) or root != expected_root):
            raise V4FixtureMaterializerViolation("fixture root does not match the validated entry")
        if task_root is not None and "mcp__hermes-tools__v4_fixture_local_state" not in _TOOLS_BY_MECHANISM[item["mechanism_class"]]:
            raise V4FixtureMaterializerViolation("task root requires the local fixture tool")
        execution_root = _task_root(task_root) if task_root is not None else None
        scenario = self._scenarios[item.row_key]
        recipe = self._manifest["turn_recipes"][item["turn_recipe"]]
        label = recipe["turn_labels"][turn_index - 1]
        bindings = tuple(binding for binding in scenario.child_bindings if binding[1] == trial_index and binding[4] == path)
        child_ids = tuple(binding[0] for binding in bindings)
        delegated_tool_path = bool(child_ids)
        surfaces = _BASE_SURFACES + tuple(_SURFACES_BY_MECHANISM[item["mechanism_class"]])
        if child_ids:
            surfaces += ("child",)
        if item["path_policy"] == "host_denial_local_recovery":
            surfaces += ("delivery_boundary",)
        approval = item["mechanism_class"] == "host_tool_pdr" or item["path_policy"] == "host_denial_local_recovery"
        sequence = ("deny", "safe_recovery") if approval and not delegated_tool_path and path == "positive" and "recovery" in item["mandatory_paths"] else (("deny",) if approval and not delegated_tool_path else ())
        allowed_tools = ("mcp__hermes-tools__delegate_task",) if delegated_tool_path else tuple(_TOOLS_BY_MECHANISM[item["mechanism_class"]])
        approval_choice = "allow" if delegated_tool_path else ("deny" if approval else "not_required")
        expected = V4HostSurfaceExpectations(
            allowed_tools, tuple(dict.fromkeys(surfaces)),
            approval_choice, sequence, len(bindings),
            tuple(binding[2] for binding in bindings), child_ids, label,
            1 if path == "positive" else 0, False,
            None if execution_root is None or delegated_tool_path else (len(surfaces), hashlib.sha256(f"{item.row_key}|{trial_index}|{turn_index}|{path}|{label}".encode()).hexdigest()),
        )
        prompt = self._prompt(item, trial_index, turn_index, path, label, expected, None if delegated_tool_path else execution_root)
        return V4FixtureMaterialization(item.row_key, expected_root, trial_index, turn_index, path, item["mechanism_class"], prompt, expected)

    def _prompt(self, item: V4LiveFixture, trial: int, turn: int, path: str, label: str, expected: V4HostSurfaceExpectations, task_root: str | None) -> V4PromptMaterial:
        tools = ",".join(expected.allowed_tool_names) or "none"
        lines = [
                "Hermes v4 clean-room synthetic fixture instruction.",
                f"Fixture root: {item['fixture_id']}", f"Row: {item.row_key}",
                f"Mechanism: {item['mechanism_class']}; path: {path}; trial: {trial}; turn: {turn}; label: {label}.",
                f"Use only Hermes-owned MCP host surfaces; allowed tools: {tools}.",
                "Do not invoke native Claude tools, Agent, or native background features; do not make direct or alternate provider calls or deliver externally. Answer this instruction through Hermes-owned surfaces.",
                f"Exercise the bounded {item['turn_recipe']} recipe and expose: {','.join(expected.required_observations)}.",
                f"Approval choice is {expected.approval_choice}; expected child count is {expected.expected_child_count}.",
                "Keep any approval denial and safe local recovery in this one parent turn; preserve one parent call.",
        ]
        if expected.expected_child_count:
            lines.append(
                f"Invoke Hermes delegate_task exactly once with a {expected.expected_child_count}-entry tasks array. "
                "Do not supply a background argument; Hermes owns the top-level dispatch mode and durable child delivery."
            )
            if item["mechanism_class"] == "host_background":
                lines.append("Expose the Hermes-owned durable background settlement lifecycle before answering.")
            else:
                lines.append("Use the completed Hermes-owned child result(s) from the normal durable session lifecycle in the answer; do not poll.")
        if task_root is not None and expected.fixture_tool_args is not None:
            count, digest = expected.fixture_tool_args
            sequence = "record (host denial expected), then check (safe recovery)" if expected.approval_sequence == ("deny", "safe_recovery") else "record (host denial expected)"
            lines.append(f"Fixture tool sequence: {sequence}; task_root={task_root}; item_count={count}; item_hash={digest}.")
        text = "\n".join(lines)
        encoded = text.encode("utf-8")
        return V4PromptMaterial(text, len(encoded), hashlib.sha256(encoded).hexdigest())

    def materialize_all(self, *, path: str = "positive") -> tuple[V4FixtureMaterialization, ...]:
        return tuple(
            self.materialize(fixture, trial_index=trial, turn_index=turn, path=path)
            for fixture in self._fixtures for trial in fixture["trial_indexes"] for turn in range(1, fixture.turn_count + 1)
        )


def materialize_v4_fixture(fixture: str | V4LiveFixture | Mapping[str, Any] | None = None, *, row_key: str | None = None, trial_index: int, turn_index: int, path: str = "positive", root: str | None = None, task_root: str | Path | None = None, materializer: V4FixtureMaterializer | None = None) -> V4FixtureMaterialization:
    """Materialize one validated fixture through the provider-free seam."""
    return (materializer or V4FixtureMaterializer()).materialize(fixture, row_key=row_key, trial_index=trial_index, turn_index=turn_index, path=path, root=root, task_root=task_root)


V4PromptBytes = V4PromptMaterial
V4HostSurfaceExpectation = V4HostSurfaceExpectations
V4MaterializedFixture = V4FixtureMaterialization
materialize_fixture = materialize_v4_fixture

__all__ = [
    "MAX_SYNTHETIC_PROMPT_BYTES", "MATERIALIZER_SCHEMA_VERSION",
    "V4FixtureMaterializer", "V4FixtureMaterializerViolation", "V4FixtureMaterialization",
    "V4HostSurfaceExpectation", "V4HostSurfaceExpectations", "V4MaterializedFixture",
    "V4PromptBytes", "V4PromptMaterial", "materialize_fixture", "materialize_v4_fixture",
]

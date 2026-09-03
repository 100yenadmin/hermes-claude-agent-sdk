"""Provider-free synthetic fixture manifest for the immutable v4 live map.

The manifest describes bounded local inputs only.  It contains no prompt
bodies, transcripts, credentials, provider requests, or expected live events.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from .hashing import sha256_value
from .v4_live_map import load_v4_live_execution_map, validate_v4_live_execution_map

FIXTURE_MANIFEST_SCHEMA_VERSION = 1
FIXTURE_MANIFEST_VERSION = "1.0.0"
CATALOG_VERSION = FIXTURE_MANIFEST_VERSION
LIVE_FIXTURE_COUNT = 70
LIVE_MAP_SHA256 = "85583a44b797a58e6a3f6fcc9f4f5234b445b49c5ab6bf38b153e872473a16ff"
_DEFAULT_MAP = Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"
_DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-fixtures.yaml"
_SAFE = re.compile(r"^[A-Za-z0-9_.:/-]{1,240}$")
_FORBIDDEN = (
    "native_agent", "native_tool", "native_settings", "direct_provider", "direct_sdk",
    "alternate_provider", "alternate_billing", "external_delivery", "raw_prompt",
    "raw_content", "raw_auth_material", "session_id", "customer_data", "unmanaged_network",
)
_MECHANISM_DEFAULTS = {
    "parent_text": {"fixture_template": "synthetic_text", "tool_intents": []},
    "parent_state": {"fixture_template": "synthetic_state", "tool_intents": ["local_state.read", "local_state.write"]},
    "host_tool_pdr": {"fixture_template": "synthetic_tool_approval", "tool_intents": ["local_tool.invoke", "local_approval.request"]},
    "host_delegate": {"fixture_template": "synthetic_delegation", "tool_intents": ["local_delegate.create"]},
    "host_background": {"fixture_template": "synthetic_background", "tool_intents": ["local_background.start"]},
    "memory_session": {"fixture_template": "synthetic_memory", "tool_intents": ["local_memory.read", "local_memory.write"]},
    "docs_skills": {"fixture_template": "synthetic_docs_skills", "tool_intents": ["local_docs.read", "local_skills.inspect"]},
    "local_cross_surface": {"fixture_template": "synthetic_cross_surface", "tool_intents": ["local_cross_surface.inspect"]},
    "adversarial_local": {"fixture_template": "synthetic_adversarial", "tool_intents": ["local_adversarial.evaluate"]},
}
_PATH_POLICIES = {
    "local_only": {"approval": "local_fixture", "denial": "local_fixture", "recovery": "local_fixture", "external_delivery": "never"},
    "host_denial_local_recovery": {"approval": "host_denial", "denial": "host_denial", "recovery": "local_recovery", "external_delivery": "never"},
}
_TURN_RECIPES = {
    "isolated_trial": {"turn_count": 1, "session_boundary": "isolated_trial", "instruction_template": "synthetic_turn", "turn_labels": ["turn"]},
    "parent_child": {"turn_count": 1, "session_boundary": "parent_child", "instruction_template": "synthetic_parent_child", "turn_labels": ["parent"]},
    "parent_two_children": {"turn_count": 1, "session_boundary": "parent_two_children", "instruction_template": "synthetic_parent_fanout", "turn_labels": ["parent"]},
    "background_one_entry_batch_join": {"turn_count": 1, "session_boundary": "background_one_entry_batch_join", "instruction_template": "synthetic_background_join", "turn_labels": ["parent"]},
    "background_child_cancel_restart": {"turn_count": 1, "session_boundary": "background_child_cancel_restart", "instruction_template": "synthetic_background_restart", "turn_labels": ["parent"]},
    "same_session_source_then_docs": {"turn_count": 2, "session_boundary": "same_session_source_then_docs", "instruction_template": "synthetic_source_docs", "turn_labels": ["source", "docs"]},
    "same_session_store_then_recall": {"turn_count": 2, "session_boundary": "same_session_store_then_recall", "instruction_template": "synthetic_store_recall", "turn_labels": ["store", "recall"]},
    "four_turns_per_isolated_trial": {"turn_count": 4, "session_boundary": "four_turns_per_isolated_trial", "instruction_template": "synthetic_memory_isolation", "turn_labels": ["seed", "isolate", "probe", "close"]},
    "before_after_restart_per_trial": {"turn_count": 2, "session_boundary": "before_after_restart_per_trial", "instruction_template": "synthetic_restart", "turn_labels": ["before_restart", "after_restart"]},
}
_TARGET = {
    "routing_provider": "claude-agent-sdk", "receipt_provider": "anthropic", "effective_model": "claude-fable-5-1",
    "execution_mode": "normal_hermes_gateway_live", "fixture_mode": "synthetic_local_only", "validation_mode": "provider_free",
    "external_delivery": "never", "billing_mode": "subscription_included",
}
_PROOF = (
    "provider_free_fixture_manifest_construction_and_validation_only",
    "manifest_contains_synthetic_identifiers_and_bounded_parameters_only",
    "normal_hermes_gateway_execution_required_for_live_calls",
    "no_external_delivery_native_route_or_customer_proof",
)
_ROOT_FIELDS = {
    "schema_version", "manifest_version", "catalog_version", "live_map_sha256", "contract_sha256",
    "predecessor_map_sha256", "target", "forbidden_routes", "mechanism_defaults", "turn_recipes",
    "path_policies", "budget", "fixtures", "proof_boundary", "manifest_sha256", "catalog_sha256",
}
_FIXTURE_FIELDS = {
    "row_key", "fixture_id", "mechanism_class", "trial_indexes", "mandatory_paths", "parent_calls", "child_calls",
    "turn_recipe", "turn_count", "session_boundary", "hermes_tool_intents", "tool_intents", "path_policy",
    "child_call_ids", "child_binding_refs",
}


class V4LiveFixtureViolation(ValueError):
    """A synthetic v4 fixture manifest is malformed or not map-bound."""


@dataclass(frozen=True, slots=True)
class V4LiveFixture:
    """Immutable view of one manifest entry."""

    data: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)

    @property
    def row_key(self) -> str:
        return self.data["row_key"]

    @property
    def turn_count(self) -> int:
        return self.data["turn_count"]

    @property
    def child_call_ids(self) -> tuple[str, ...]:
        return tuple(self.data["child_call_ids"])


@dataclass(frozen=True, slots=True)
class V4LiveFixtureManifest:
    """Immutable manifest view with deterministic hash accessors."""

    data: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)

    @property
    def fixtures(self) -> tuple[V4LiveFixture, ...]:
        return tuple(V4LiveFixture(item) for item in self.data["fixtures"])

    @property
    def rows(self) -> tuple[V4LiveFixture, ...]:
        return self.fixtures

    @property
    def manifest_sha256(self) -> str:
        return self.data["manifest_sha256"]

    @property
    def catalog_sha256(self) -> str:
        return self.data["catalog_sha256"]


def _closed(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise V4LiveFixtureViolation(f"{field} fields are not closed")
    return dict(value)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 240:
        raise V4LiveFixtureViolation(f"{field} is not a safe fixture identifier")
    if _SAFE.fullmatch(value) is None or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
        raise V4LiveFixtureViolation(f"{field} contains unsafe fixture data")
    if any(token in value.casefold() for token in ("raw_prompt", "raw_content", "raw_auth", "session_id", "customer_data", "credential", "cookie", "oauth", "api_key", "private_endpoint")):
        raise V4LiveFixtureViolation(f"{field} contains forbidden fixture data")
    return value


def _seq(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise V4LiveFixtureViolation(f"{field} must be a sequence")
    return list(value)


def _map(value: Mapping[str, Any] | str | Path | None, map_path: str | Path | None) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    source = Path(map_path).expanduser().resolve() if map_path is not None else _DEFAULT_MAP
    try:
        document = load_v4_live_execution_map(value) if isinstance(value, (str, Path)) else load_v4_live_execution_map(source) if value is None else dict(value)
        accounting = validate_v4_live_execution_map(document, map_path=source)
    except Exception as exc:
        raise V4LiveFixtureViolation("corrected v4 live map could not be loaded and validated") from exc
    if accounting.get("map_sha256") != LIVE_MAP_SHA256:
        raise V4LiveFixtureViolation("fixture manifest requires the frozen corrected v4 live map")
    return document, source, accounting


def _expected(value: Mapping[str, Any] | str | Path | None = None, *, map_path: str | Path | None = None) -> dict[str, Any]:
    live_map, _, accounting = _map(value, map_path)
    children: dict[str, list[str]] = {}
    for child in live_map["child_calls"]:
        children.setdefault(child["row_key"], []).append(child["call_id"])
    fixtures = []
    for row in live_map["rows"]:
        key = f"{row['source_pack']}/{row['source_item_id']}"; recipe = row["session_boundary"]
        defaults = _MECHANISM_DEFAULTS[row["mechanism_class"]]; child_ids = children.get(key, [])
        turn_count = row["parent_calls"] // len(row["required_trial_indexes"])
        path_policy = "host_denial_local_recovery" if row["delivery_mode"] == "host_denial_local_recovery" else "local_only"
        fixtures.append({
            "row_key": key, "fixture_id": f"synthetic/{key}", "mechanism_class": row["mechanism_class"],
            "trial_indexes": list(row["required_trial_indexes"]), "mandatory_paths": list(row["mandatory_paths"]),
            "parent_calls": row["parent_calls"], "child_calls": row["child_calls"], "turn_recipe": recipe,
            "turn_count": turn_count, "session_boundary": recipe, "hermes_tool_intents": list(defaults["tool_intents"]),
            "tool_intents": list(defaults["tool_intents"]), "path_policy": path_policy,
            "child_call_ids": list(child_ids), "child_binding_refs": list(child_ids),
        })
    base = {
        "schema_version": FIXTURE_MANIFEST_SCHEMA_VERSION, "manifest_version": FIXTURE_MANIFEST_VERSION,
        "catalog_version": CATALOG_VERSION, "live_map_sha256": accounting["map_sha256"],
        "contract_sha256": live_map["source"]["contract_sha256"], "predecessor_map_sha256": live_map["source"]["predecessor_map_sha256"],
        "target": dict(_TARGET), "forbidden_routes": list(_FORBIDDEN), "mechanism_defaults": {key: dict(item) for key, item in _MECHANISM_DEFAULTS.items()},
        "turn_recipes": {key: dict(item) for key, item in _TURN_RECIPES.items()}, "path_policies": {key: dict(item) for key, item in _PATH_POLICIES.items()},
        "budget": {"parent_calls": accounting["parent_calls"], "child_calls": accounting["child_calls"], "total_calls": accounting["total_calls"], "turn_budget": accounting["parent_calls"] + accounting["child_calls"] + accounting["reserve_calls"], "reserve_calls": accounting["reserve_calls"]},
        "fixtures": fixtures, "proof_boundary": list(_PROOF),
    }
    digest = sha256_value(base); base["manifest_sha256"] = digest; base["catalog_sha256"] = digest
    return base


def _validate_shape(document: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    _closed(document, _ROOT_FIELDS, "fixture manifest")
    if (document["schema_version"], document["manifest_version"], document["catalog_version"]) != (FIXTURE_MANIFEST_SCHEMA_VERSION, FIXTURE_MANIFEST_VERSION, CATALOG_VERSION):
        raise V4LiveFixtureViolation("fixture manifest version is unsupported")
    for field in ("live_map_sha256", "contract_sha256", "predecessor_map_sha256", "manifest_sha256", "catalog_sha256"):
        if not isinstance(document[field], str) or len(document[field]) != 64 or any(char not in "0123456789abcdef" for char in document[field]):
            raise V4LiveFixtureViolation(f"{field} is not a lowercase SHA-256 digest")
    forbidden = document["forbidden_routes"]; proof = document["proof_boundary"]
    if not isinstance(forbidden, Sequence) or isinstance(forbidden, (str, bytes, bytearray)) or not isinstance(proof, Sequence) or isinstance(proof, (str, bytes, bytearray)):
        raise V4LiveFixtureViolation("fixture route or proof boundary is malformed")
    if document["target"] != _TARGET or tuple(forbidden) != _FORBIDDEN or tuple(proof) != _PROOF:
        raise V4LiveFixtureViolation("fixture route or proof boundary is unsafe")
    if document["mechanism_defaults"] != _MECHANISM_DEFAULTS or document["turn_recipes"] != _TURN_RECIPES or document["path_policies"] != _PATH_POLICIES:
        raise V4LiveFixtureViolation("fixture defaults or recipes drifted")
    budget = document["budget"]
    if budget != expected["budget"]:
        raise V4LiveFixtureViolation("fixture call budget drifted")
    raw = _seq(document["fixtures"], "fixtures")
    if len(raw) != LIVE_FIXTURE_COUNT:
        raise V4LiveFixtureViolation("fixture manifest must contain exactly 70 entries")
    keys = []
    for index, item in enumerate(raw):
        fixture = _closed(item, _FIXTURE_FIELDS, f"fixtures[{index}]"); keys.append(_id(fixture["row_key"], "fixture.row_key"))
        for field in ("fixture_id", "mechanism_class", "turn_recipe", "session_boundary", "path_policy"):
            _id(fixture[field], f"fixture.{field}")
        for field in ("mandatory_paths", "hermes_tool_intents", "tool_intents", "child_call_ids", "child_binding_refs"):
            values = _seq(fixture[field], f"fixture.{field}")
            if any(not isinstance(entry, str) for entry in values) or len(values) != len(set(values)):
                raise V4LiveFixtureViolation(f"fixture.{field} is not a unique safe list")
            for entry in values:
                _id(entry, f"fixture.{field}")
        trials = _seq(fixture["trial_indexes"], "fixture.trial_indexes")
        if any(type(entry) is not int for entry in trials) or len(trials) != len(set(trials)):
            raise V4LiveFixtureViolation("fixture.trial_indexes is not a unique integer list")
        if any(type(fixture[field]) is not int or fixture[field] < 0 for field in ("turn_count", "parent_calls", "child_calls")):
            raise V4LiveFixtureViolation("fixture call or turn counts are invalid")
        if not all(type(entry) is int and entry >= 1 for entry in fixture["trial_indexes"]):
            raise V4LiveFixtureViolation("fixture trial indexes are invalid")
        if fixture["hermes_tool_intents"] != fixture["tool_intents"] or fixture["child_call_ids"] != fixture["child_binding_refs"]:
            raise V4LiveFixtureViolation("fixture aliases are not identical")
    if len(set(keys)) != LIVE_FIXTURE_COUNT:
        raise V4LiveFixtureViolation("fixture rows contain duplicate keys")
    if document["live_map_sha256"] != expected["live_map_sha256"] or document["contract_sha256"] != expected["contract_sha256"] or document["predecessor_map_sha256"] != expected["predecessor_map_sha256"]:
        raise V4LiveFixtureViolation("fixture manifest is not bound to the immutable source artifacts")


def validate_v4_live_fixture_manifest(value: V4LiveFixtureManifest | Mapping[str, Any], *, live_map: Mapping[str, Any] | str | Path | None = None, map_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the exact map-bound fixture catalog without invoking a provider."""
    if isinstance(value, V4LiveFixtureManifest):
        document = value.to_dict()
    elif isinstance(value, Mapping):
        document = dict(value)
    else:
        raise V4LiveFixtureViolation("fixture manifest must be a mapping")
    expected = _expected(live_map, map_path=map_path); _validate_shape(document, expected)
    if document["fixtures"] != expected["fixtures"]:
        raise V4LiveFixtureViolation("fixture entries are missing, duplicated, unknown, or drifted from the map")
    digest = sha256_value({key: document[key] for key in _ROOT_FIELDS if key not in {"manifest_sha256", "catalog_sha256"}})
    if document["manifest_sha256"] != expected["manifest_sha256"] or document["catalog_sha256"] != expected["catalog_sha256"] or digest != expected["manifest_sha256"]:
        raise V4LiveFixtureViolation("fixture catalog hash is not deterministic")
    return {"manifest_sha256": expected["manifest_sha256"], "catalog_sha256": expected["catalog_sha256"], "live_map_sha256": expected["live_map_sha256"], "provider_live_rows": LIVE_FIXTURE_COUNT, **expected["budget"]}


def build_v4_live_fixture_manifest(live_map: Mapping[str, Any] | str | Path | None = None, *, map_path: str | Path | None = None) -> V4LiveFixtureManifest:
    """Derive the immutable synthetic fixture manifest from the corrected map."""
    document = _expected(live_map, map_path=map_path); validate_v4_live_fixture_manifest(document, live_map=live_map, map_path=map_path)
    return V4LiveFixtureManifest(document)


def load_v4_live_fixture_manifest(path: str | Path | None = None) -> V4LiveFixtureManifest:
    """Load and validate the checked-in provider-free fixture manifest."""
    source = Path(path).expanduser().resolve() if path is not None else _DEFAULT_MANIFEST
    if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
        raise V4LiveFixtureViolation("fixture manifest is not a bounded regular file")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise V4LiveFixtureViolation("fixture manifest cannot be parsed") from exc
    validate_v4_live_fixture_manifest(document, map_path=_DEFAULT_MAP)
    return V4LiveFixtureManifest(dict(document))


load_live_fixture_manifest = load_v4_live_fixture_manifest
validate_live_fixture_manifest = validate_v4_live_fixture_manifest

__all__ = [
    "CATALOG_VERSION", "FIXTURE_MANIFEST_SCHEMA_VERSION", "FIXTURE_MANIFEST_VERSION", "LIVE_FIXTURE_COUNT", "LIVE_MAP_SHA256",
    "V4LiveFixture", "V4LiveFixtureManifest", "V4LiveFixtureViolation", "build_v4_live_fixture_manifest",
    "load_live_fixture_manifest", "load_v4_live_fixture_manifest", "validate_live_fixture_manifest", "validate_v4_live_fixture_manifest",
]

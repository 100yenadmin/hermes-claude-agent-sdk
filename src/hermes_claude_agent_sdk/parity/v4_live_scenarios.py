"""Provider-free synthetic inputs for the immutable v4 live row ledger.

This catalog describes what a later normal-Hermes runner must exercise and
observe.  It deliberately contains no prompts, runtime events, outcomes, or
provider invocation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import sha256_value
from .v4_live_map import load_v4_live_execution_map, validate_v4_live_execution_map

SCENARIO_SCHEMA_VERSION = 1
SCENARIO_CATALOG_VERSION = "1.0.0"
LIVE_SCENARIO_COUNT = 70
LIVE_MAP_SHA256 = "85583a44b797a58e6a3f6fcc9f4f5234b445b49c5ab6bf38b153e872473a16ff"
_DEFAULT_MAP = Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"
_PATHS = {"positive", "denial", "recovery"}
_SURFACE_EXTRAS = {
    "parent_text": (), "parent_state": ("state",), "host_tool_pdr": ("tool", "approval"),
    "host_delegate": ("delegate",), "host_background": ("background",),
    "memory_session": ("memory",), "docs_skills": ("docs", "skills"),
    "local_cross_surface": ("cross_surface",), "adversarial_local": ("adversarial",),
}
_TARGET = {
    "execution_mode": "normal_hermes_gateway_live", "input_mode": "synthetic_local_fixture",
    "observation_mode": "host_surfaces_only", "external_delivery": "never",
    "catalog_invokes_provider": False,
}
_FORBIDDEN = (
    "native_agent", "native_claude_tools", "native_settings", "prompt_preset",
    "alternate_provider", "direct_provider", "direct_sdk", "raw_auth_material",
    "unmanaged_network", "external_delivery", "metered_api", "api_key",
    "credential", "extra_usage", "glm",
)
_PROOF = (
    "provider_free_catalog_construction_and_validation_only",
    "catalog_contains_no_expected_events_or_live_outcomes",
    "normal_hermes_gateway_execution_required_for_live_calls",
    "no_external_delivery_or_customer_proof",
)
_SCENARIO_FIELDS = {
    "row_key", "source_pack", "source_item_id", "predecessor_execution_id", "feature_id",
    "mechanism_class", "input_kind", "input_ref", "operation", "local_only",
    "mandatory_paths", "trial_indexes", "turn_count", "path_call_budget", "required_surfaces", "parent_calls",
    "child_calls", "bundle_mode", "session_boundary", "explicit_child_calls", "child_call_ids",
    "child_bindings", "semantic_aliases",
}
_CHILD_FIELDS = {"call_id", "trial_index", "child_ordinal", "child_count", "path"}


class V4LiveScenarioViolation(ValueError):
    """A synthetic v4 scenario catalog is malformed or not map-bound."""


@dataclass(frozen=True, slots=True)
class V4LiveScenario:
    row_key: str
    source_pack: str
    source_item_id: str
    predecessor_execution_id: str
    feature_id: str
    mechanism_class: str
    input_kind: str
    input_ref: str
    operation: str
    local_only: bool
    mandatory_paths: tuple[str, ...]
    trial_indexes: tuple[int, ...]
    turn_count: int
    path_call_budget: tuple[tuple[str, int], ...]
    required_surfaces: tuple[str, ...]
    parent_calls: int
    child_calls: int
    bundle_mode: str
    session_boundary: str
    explicit_child_calls: bool
    child_call_ids: tuple[str, ...]
    child_bindings: tuple[tuple[str, int, int, int, str], ...]
    semantic_aliases: tuple[str, ...]

    @property
    def path_bundle(self) -> tuple[str, ...]:
        return self.mandatory_paths

    @property
    def expected_path_bundle(self) -> tuple[str, ...]:
        return self.mandatory_paths

    @property
    def required_hermes_surfaces(self) -> tuple[str, ...]:
        return self.required_surfaces

    @property
    def turns(self) -> int:
        return self.turn_count

    @property
    def child_calls_authorized(self) -> bool:
        return self.explicit_child_calls

    @property
    def explicit_child_calls_allowed(self) -> bool:
        return self.explicit_child_calls

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    @property
    def provider_calls_by_path(self) -> dict[str, int]:
        return dict(self.path_call_budget)

    def to_dict(self) -> dict[str, Any]:
        bindings = [
            {"call_id": call, "trial_index": trial, "child_ordinal": ordinal,
             "child_count": count, "path": path}
            for call, trial, ordinal, count, path in self.child_bindings
        ]
        return {
            "row_key": self.row_key, "source_pack": self.source_pack, "source_item_id": self.source_item_id,
            "predecessor_execution_id": self.predecessor_execution_id, "feature_id": self.feature_id,
            "mechanism_class": self.mechanism_class, "input_kind": self.input_kind, "input_ref": self.input_ref,
            "operation": self.operation, "local_only": self.local_only, "mandatory_paths": list(self.mandatory_paths),
            "trial_indexes": list(self.trial_indexes), "turn_count": self.turn_count,
            "path_call_budget": dict(self.path_call_budget),
            "required_surfaces": list(self.required_surfaces), "parent_calls": self.parent_calls,
            "child_calls": self.child_calls, "bundle_mode": self.bundle_mode,
            "session_boundary": self.session_boundary, "explicit_child_calls": self.explicit_child_calls,
            "child_call_ids": list(self.child_call_ids), "child_bindings": bindings,
            "semantic_aliases": list(self.semantic_aliases),
        }


@dataclass(frozen=True, slots=True)
class V4LiveScenarioCatalog:
    live_map_sha256: str
    contract_sha256: str
    predecessor_map_sha256: str
    scenarios: tuple[V4LiveScenario, ...]
    parent_calls: int = 120
    child_calls: int = 16
    total_calls: int = 136
    turn_budget: int = 180
    reserve_calls: int = 44

    @property
    def rows(self) -> tuple[V4LiveScenario, ...]:
        return self.scenarios

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCENARIO_SCHEMA_VERSION, "catalog_version": SCENARIO_CATALOG_VERSION,
            "live_map_sha256": self.live_map_sha256, "contract_sha256": self.contract_sha256,
            "predecessor_map_sha256": self.predecessor_map_sha256,
            "target": dict(_TARGET), "forbidden_routes": list(_FORBIDDEN),
            "budget": {"parent_calls": self.parent_calls, "child_calls": self.child_calls,
                       "total_calls": self.total_calls, "turn_budget": self.turn_budget,
                       "reserve_calls": self.reserve_calls},
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
            "proof_boundary": list(_PROOF),
        }

    @property
    def catalog_sha256(self) -> str:
        return sha256_value(self._payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload["catalog_sha256"] = self.catalog_sha256
        return payload


def _seq(value: Any, field: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise V4LiveScenarioViolation(f"{field} must be a sequence")
    return tuple(value)


def _binding(value: Any, field: str) -> tuple[str, int, int, int, str]:
    if not isinstance(value, Mapping) or set(value) != _CHILD_FIELDS:
        raise V4LiveScenarioViolation(f"{field} is not a closed child binding")
    values = tuple(value[key] for key in ("call_id", "trial_index", "child_ordinal", "child_count", "path"))
    if not isinstance(values[0], str) or not all(type(item) is int for item in values[1:4]) or not isinstance(values[4], str):
        raise V4LiveScenarioViolation(f"{field} has an invalid child binding")
    return values  # type: ignore[return-value]


def _scenario(value: Any) -> V4LiveScenario:
    if isinstance(value, V4LiveScenario):
        return value
    if not isinstance(value, Mapping) or set(value) != _SCENARIO_FIELDS:
        raise V4LiveScenarioViolation("scenario fields are not closed")
    try:
        bindings = tuple(_binding(item, "child_bindings") for item in _seq(value["child_bindings"], "child_bindings"))
        return V4LiveScenario(
            value["row_key"], value["source_pack"], value["source_item_id"], value["predecessor_execution_id"],
            value["feature_id"], value["mechanism_class"], value["input_kind"], value["input_ref"],
            value["operation"], value["local_only"], tuple(_seq(value["mandatory_paths"], "mandatory_paths")),
            tuple(_seq(value["trial_indexes"], "trial_indexes")), value["turn_count"],
            tuple((path, calls) for path, calls in value["path_call_budget"].items()) if isinstance(value["path_call_budget"], Mapping) else (),
            tuple(_seq(value["required_surfaces"], "required_surfaces")), value["parent_calls"], value["child_calls"],
            value["bundle_mode"], value["session_boundary"], value["explicit_child_calls"],
            tuple(_seq(value["child_call_ids"], "child_call_ids")), bindings,
            tuple(_seq(value["semantic_aliases"], "semantic_aliases")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V4LiveScenarioViolation("scenario contains an invalid field") from exc


def _map(value: Mapping[str, Any] | str | Path | None, map_path: str | Path | None) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    source = Path(map_path).expanduser().resolve() if map_path is not None else _DEFAULT_MAP
    try:
        document = load_v4_live_execution_map(source) if value is None else load_v4_live_execution_map(value) if isinstance(value, (str, Path)) else dict(value)
        accounting = validate_v4_live_execution_map(document, map_path=source)
    except Exception as exc:
        raise V4LiveScenarioViolation("corrected v4 live map could not be loaded and validated") from exc
    if accounting.get("map_sha256") != LIVE_MAP_SHA256:
        raise V4LiveScenarioViolation("scenario catalog requires the frozen corrected v4 live map")
    return document, source, accounting


def _from_map(document: Mapping[str, Any], accounting: Mapping[str, Any]) -> V4LiveScenarioCatalog:
    children: dict[str, list[Mapping[str, Any]]] = {}
    for child in document["child_calls"]:
        children.setdefault(child["row_key"], []).append(child)
    scenarios = []
    for row in document["rows"]:
        key = f"{row['source_pack']}/{row['source_item_id']}"
        trials = tuple(row["required_trial_indexes"]); parent_calls = row["parent_calls"]
        if not trials or type(parent_calls) is not int or parent_calls < len(trials) or parent_calls % len(trials):
            raise V4LiveScenarioViolation(f"{key} has an unbounded turn bundle")
        turns = parent_calls // len(trials)
        if not 1 <= turns <= 4:
            raise V4LiveScenarioViolation(f"{key} exceeds the 1-4 turn fixture bound")
        bound = children.get(key, [])
        bindings = tuple((item["call_id"], item["trial_index"], item["child_ordinal"], item["child_count"], item["path"]) for item in bound)
        path_call_budget = tuple((path, turns if path == "positive" else 0) for path in row["mandatory_paths"])
        surfaces = ("session", "prompt", "transcript", "stream") + _SURFACE_EXTRAS[row["mechanism_class"]]
        if row["child_calls"]:
            surfaces += ("child",)
        if row["delivery_mode"] == "host_denial_local_recovery":
            surfaces += ("delivery_boundary",)
        scenarios.append(V4LiveScenario(
            key, row["source_pack"], row["source_item_id"], row["predecessor_execution_id"], row["feature_id"],
            row["mechanism_class"], "synthetic_local_fixture", f"synthetic/{key}", row["delivery_mode"], True,
            tuple(row["mandatory_paths"]), trials, turns, path_call_budget, tuple(dict.fromkeys(surfaces)), parent_calls,
            row["child_calls"], row["bundle_mode"], row["session_boundary"], bool(row["child_calls"]),
            tuple(item["call_id"] for item in bound), bindings, tuple(row["semantic_aliases"]),
        ))
    catalog = V4LiveScenarioCatalog(
        accounting["map_sha256"], document["source"]["contract_sha256"], document["source"]["predecessor_map_sha256"],
        tuple(scenarios), accounting["parent_calls"], accounting["child_calls"], accounting["total_calls"],
        accounting["parent_calls"] + accounting["child_calls"] + accounting["reserve_calls"], accounting["reserve_calls"],
    )
    if len(catalog.scenarios) != LIVE_SCENARIO_COUNT:
        raise V4LiveScenarioViolation("scenario catalog must contain exactly 70 rows")
    return catalog


def build_v4_live_scenario_catalog(live_map: Mapping[str, Any] | str | Path | None = None, *, map_path: str | Path | None = None) -> V4LiveScenarioCatalog:
    """Build an immutable catalog from the corrected, provider-free map."""
    document, _, accounting = _map(live_map, map_path)
    catalog = _from_map(document, accounting)
    validate_v4_live_scenario_catalog(catalog, live_map=document, map_path=map_path)
    return catalog


def load_v4_live_scenario_catalog(path: str | Path | None = None) -> V4LiveScenarioCatalog:
    """Load and validate the frozen corrected map, then derive its catalog."""
    map_path = Path(path).expanduser().resolve() if path is not None else _DEFAULT_MAP
    return build_v4_live_scenario_catalog(map_path=map_path)


def validate_v4_live_scenario_catalog(value: V4LiveScenarioCatalog | Mapping[str, Any], *, live_map: Mapping[str, Any] | str | Path | None = None, map_path: str | Path | None = None) -> dict[str, Any]:
    """Validate row bijection, exact map binding, budgets, and route restrictions."""
    document = value.to_dict() if isinstance(value, V4LiveScenarioCatalog) else dict(value)
    expected_map, _, accounting = _map(live_map, map_path)
    expected = _from_map(expected_map, accounting)
    fields = {"schema_version", "catalog_version", "live_map_sha256", "contract_sha256", "predecessor_map_sha256", "target", "forbidden_routes", "budget", "scenarios", "proof_boundary", "catalog_sha256"}
    if set(document) != fields or document["schema_version"] != SCENARIO_SCHEMA_VERSION or document["catalog_version"] != SCENARIO_CATALOG_VERSION:
        raise V4LiveScenarioViolation("catalog envelope is not closed")
    if document["target"] != _TARGET or tuple(document["forbidden_routes"]) != _FORBIDDEN or tuple(document["proof_boundary"]) != _PROOF:
        raise V4LiveScenarioViolation("catalog route or proof boundary is unsafe")
    if document["live_map_sha256"] != expected.live_map_sha256 or document["contract_sha256"] != expected.contract_sha256 or document["predecessor_map_sha256"] != expected.predecessor_map_sha256:
        raise V4LiveScenarioViolation("catalog is not bound to the corrected v4 source artifacts")
    budget = document["budget"]
    expected_budget = {"parent_calls": 120, "child_calls": 16, "total_calls": 136, "turn_budget": 180, "reserve_calls": 44}
    if not isinstance(budget, Mapping) or dict(budget) != expected_budget:
        raise V4LiveScenarioViolation("catalog budget is not the frozen row-bound envelope")
    raw = document["scenarios"]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) != LIVE_SCENARIO_COUNT:
        raise V4LiveScenarioViolation("catalog must contain exactly 70 scenarios")
    parsed = tuple(_scenario(item) for item in raw)
    keys = tuple(item.row_key for item in parsed)
    expected_keys = tuple(item.row_key for item in expected.scenarios)
    if len(set(keys)) != LIVE_SCENARIO_COUNT or set(keys) != set(expected_keys) or parsed != expected.scenarios:
        raise V4LiveScenarioViolation("catalog rows are missing, duplicated, unknown, or drifted")
    if document["catalog_sha256"] != expected.catalog_sha256 or document["catalog_sha256"] != sha256_value({key: document[key] for key in fields if key != "catalog_sha256"}):
        raise V4LiveScenarioViolation("catalog is not deterministic")
    return {"catalog_sha256": expected.catalog_sha256, "live_map_sha256": expected.live_map_sha256, "contract_sha256": expected.contract_sha256, "provider_live_rows": len(parsed), "parent_calls": expected.parent_calls, "child_calls": expected.child_calls, "total_calls": expected.total_calls, "turn_budget": expected.turn_budget}


load_live_scenario_catalog = load_v4_live_scenario_catalog
validate_live_scenario_catalog = validate_v4_live_scenario_catalog

__all__ = [
    "LIVE_MAP_SHA256", "LIVE_SCENARIO_COUNT", "SCENARIO_CATALOG_VERSION", "SCENARIO_SCHEMA_VERSION",
    "V4LiveScenario", "V4LiveScenarioCatalog", "V4LiveScenarioViolation", "build_v4_live_scenario_catalog",
    "load_live_scenario_catalog", "load_v4_live_scenario_catalog", "validate_live_scenario_catalog",
    "validate_v4_live_scenario_catalog",
]

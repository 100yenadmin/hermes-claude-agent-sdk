"""Strict, offline validators for parity-v4 packet metadata."""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any
from .canonical import CanonicalizationError, SDK_EVENT_CODES, TRACE_REGISTRY, canonical_sha256, validate_identifier, validate_sha256
class PacketValidationError(CanonicalizationError):
    pass

PATHS = ("positive", "denial", "recovery")
EXPECTED = frozenset(("PASS", "EXPECTED_NEGATIVE", "NOT_APPLICABLE"))
OBSERVED = frozenset(("PASS", "EXPECTED_NEGATIVE", "VERIFIED_FAILURE", "ENVIRONMENT_BLOCKED", "PENDING", "NOT_APPLICABLE"))
QUALIFICATIONS = frozenset(("PASS", "EXPECTED_NEGATIVE", "FAIL", "BLOCKED", "PENDING", "NOT_APPLICABLE"))
SCOPES = frozenset(("isolated_cell", "one_logical_session"))
TERMINAL = frozenset(("complete", "cancelled", "failed", "not_applicable"))
REDACTION_CATEGORIES = frozenset(("auth", "credential", "secret", "prompt", "transcript", "session", "resume_state", "provider_payload", "fixture_content", "customer_identifier", "customer_data", "environment", "exception", "filesystem_path", "path", "tool_args", "tool_results", "headers", "cookies"))
FORBIDDEN = frozenset(("raw", "content", "prompt", "transcript", "session_token", "resume_state", "auth", "credential", "secret", "provider_payload", "fixture_content", "exception", "environment", "filesystem_path", "path", "customer", "customer_identifier", "customer_data", "tool_args", "tool_results", "arguments", "headers", "cookies", "fixture_bytes"))
GRADE_KEYS = frozenset(("status", "cell_qualifications", "required_cell_count", "observed_cell_count", "required_pass_count", "observed_pass_count", "expected_negative_count", "verified_failure_count", "environment_blocked_count", "pending_count", "pass_caret_3", "pass_at_3", "candidate_consistent", "terminal_events_exact", "inventory_exact", "billing_safe", "resume_safe", "isolation_safe", "sdk_ledger", "result_bijection"))
def _fail(message: str) -> None:
    raise PacketValidationError(message)

def _safe(value: Any) -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or key in FORBIDDEN for key in value): _fail("packet contains forbidden content")
    for item in value.values() if isinstance(value, Mapping) else value if isinstance(value, list) else (): _safe(item)

def _obj(value: Any, keys: set[str] | frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys): _fail(f"{label} must have exactly {sorted(keys)}")
    _safe(value)
    return dict(value)

def _map(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping): _fail(f"{label} must be an object")
    return value

def _enum(value: Any, allowed: Sequence[str] | set[str] | frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed: _fail(f"{label} is outside its closed enum")
    return value

def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool): _fail(f"{label} must be boolean")
    return value

def _int(value: Any, label: str, low: int = 0, high: int = 4096) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high: _fail(f"{label} is outside its integer bound")
    return value

def _sha(value: Any, label: str, nullable: bool = False) -> str | None:
    if nullable and value is None: return None
    try:
        return validate_sha256(value, field=label)
    except CanonicalizationError as exc: raise PacketValidationError(str(exc)) from exc

def _sha1(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(char not in "0123456789abcdef" for char in value): _fail(f"{label} must be lowercase SHA-1")
    return value

def _version(value: Any, label: str) -> str:
    value = _id(value, label); _fail(f"{label} must be semver") if len(value.split(".")) != 3 or any(not part.isdigit() for part in value.split(".")) else None; return value

def _id(value: Any, label: str) -> str:
    try:
        return validate_identifier(value, field=label, max_length=128)
    except CanonicalizationError as exc: raise PacketValidationError(str(exc)) from exc

def _pref(value: Any, prefix: str, label: str) -> str:
    value = _id(value, label)
    if not value.startswith(prefix): _fail(f"{label} has an invalid prefix")
    return value

def _arr(value: Any, label: str, maximum: int = 4096) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum: _fail(f"{label} must be a bounded array")
    return value

def _omit(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in keys}

def _sorted(values: list[str], label: str) -> None:
    if values != sorted(values) or len(values) != len(set(values)): _fail(f"{label} must be sorted and unique")

def _derived(value: Any, label: str) -> Mapping[str, Any]:
    result = _map(value, label)
    if len(result) > 4096: _fail(f"{label} is oversized")
    for key, item in result.items():
        if key in FORBIDDEN: _fail(f"{label} contains forbidden content")
        _id(key, f"{label} key")
        if isinstance(item, Mapping): _derived(item, f"{label}.{key}")
        elif isinstance(item, list): [_id(child, f"{label}.{key}") for child in item]
        elif isinstance(item, bool): continue
        elif isinstance(item, int): _int(item, f"{label}.{key}")
        elif isinstance(item, str): _enum(item, OBSERVED | QUALIFICATIONS | {"CLEAR", "STOP"}, f"{label}.{key}")
        else: _fail(f"{label}.{key} is not safe metadata")
    return result

def _terminal(value: Any, label: str) -> dict[str, Any]:
    result = _obj(value, {"kind", "count"}, label)
    _enum(result["kind"], TERMINAL, f"{label}.kind"); _int(result["count"], f"{label}.count", 0, 1)
    return result

def derive_path_qualification(expected: str, status: str) -> str:
    _enum(expected, EXPECTED, "expected_outcome"); _enum(status, OBSERVED, "path status")
    if status == "ENVIRONMENT_BLOCKED": return "BLOCKED"
    if status == "PENDING": return "PENDING"
    if status == "NOT_APPLICABLE": return "NOT_APPLICABLE" if expected == status else "FAIL"
    return status if status == expected else "FAIL"

def validate_redaction(value: Any) -> dict[str, Any]:
    result = _obj(value, {"profile", "forbidden_field_count", "omitted_categories", "secret_scan"}, "redaction")
    if result["profile"] != "v3-safe": _fail("redaction profile is invalid")
    _int(result["forbidden_field_count"], "redaction.forbidden_field_count")
    categories = _arr(result["omitted_categories"], "redaction.omitted_categories", 19)
    if any(not isinstance(item, str) or item not in REDACTION_CATEGORIES for item in categories): _fail("redaction category is not in the closed enum")
    _sorted(categories, "redaction.omitted_categories")
    _enum(result["secret_scan"], ("PASS", "FAIL"), "redaction.secret_scan")
    if result["forbidden_field_count"] or result["secret_scan"] != "PASS":
        _fail("redaction is not safe")
    return result

def _trace_codes(value: Any, label: str) -> list[str]:
    codes = _arr(value, label, 256)
    if any(not isinstance(code, str) or code not in TRACE_REGISTRY for code in codes): _fail(f"{label} contains an unknown trace code")
    return codes

def validate_path_status(value: Any, expected: str, label: str = "path_status") -> dict[str, Any]:
    result = _obj(value, {"status", "observed_trace_codes", "terminal", "qualification"}, label)
    status = _enum(result["status"], OBSERVED, f"{label}.status")
    traces = _trace_codes(result["observed_trace_codes"], f"{label}.observed_trace_codes")
    terminal = _terminal(result["terminal"], f"{label}.terminal")
    expected_kind, expected_count = {"PASS": ("complete", 1), "EXPECTED_NEGATIVE": ("failed", 1), "VERIFIED_FAILURE": ("failed", 1), "ENVIRONMENT_BLOCKED": ("not_applicable", 0), "PENDING": ("not_applicable", 0), "NOT_APPLICABLE": ("not_applicable", 0)}[status]
    if terminal["count"] != expected_count or terminal["kind"] != expected_kind and not (status in {"EXPECTED_NEGATIVE", "VERIFIED_FAILURE"} and terminal["kind"] == "cancelled"): _fail(f"{label}.terminal does not match status")
    terminals = [code for code in traces if code.startswith("terminal.")]
    if status in {"ENVIRONMENT_BLOCKED", "PENDING", "NOT_APPLICABLE"} and traces: _fail(f"{label} non-executed status has a trace")
    if status in {"PASS", "EXPECTED_NEGATIVE", "VERIFIED_FAILURE"} and terminals != [f"terminal.{terminal['kind']}"]: _fail(f"{label} must contain its one terminal trace")
    qualification = derive_path_qualification(expected, status)
    if result["qualification"] != qualification: _fail(f"{label}.qualification is not derived")
    return result

def _state(value: Any, label: str) -> dict[str, Any]:
    result = _obj(value, {"lifecycle", "approval", "tool", "resume", "billing", "side_effect_count", "boundary_sha256"}, label)
    for key, allowed in (("lifecycle", ("fresh", "bound", "running", "completed", "failed", "cancelled", "closed")), ("approval", ("not_required", "pending", "granted", "denied", "late_rejected")), ("tool", ("none", "requested", "executed", "denied", "cancelled", "failed", "recovered")), ("resume", ("absent", "supplied", "accepted", "rejected")), ("billing", ("included", "blocked", "unknown", "not_applicable"))): _enum(result[key], allowed, f"{label}.{key}")
    _int(result["side_effect_count"], f"{label}.side_effect_count")
    _sha(result["boundary_sha256"], f"{label}.boundary_sha256", True)
    return result

def _path(value: Any, label: str) -> dict[str, Any]:
    keys = {"required", "expected_outcome", "trace_codes", "terminal", "tool_calls", "side_effect_count", "sdk_events", "state_before", "state_after"}
    result = _obj(value, keys, label)
    _bool(result["required"], f"{label}.required")
    expected = _enum(result["expected_outcome"], EXPECTED, f"{label}.expected_outcome")
    _trace_codes(result["trace_codes"], f"{label}.trace_codes")
    terminal = _terminal(result["terminal"], f"{label}.terminal")
    calls = _arr(result["tool_calls"], f"{label}.tool_calls", 32)
    for ordinal, call in enumerate(calls, 1):
        item = _obj(call, {"ordinal", "name", "schema_sha256", "outcome", "request_id"}, f"{label}.tool_calls[{ordinal}]")
        if item["ordinal"] != ordinal: _fail(f"{label} tool ordinal is not contiguous")
        _id(item["name"], "tool name")
        _sha(item["schema_sha256"], "tool schema hash")
        _enum(item["outcome"], {"requested", "executed", "denied", "cancelled", "failed", "recovered"}, "tool outcome")
        request = _obj(item["request_id"], {"mode", "sha256"}, "request_id")
        mode = _enum(request["mode"], {"none", "required"}, "request_id.mode")
        _sha(request["sha256"], "request_id.sha256", True)
        if mode == "none" and request["sha256"] is not None: _fail("request_id mode none has a digest")
    _int(result["side_effect_count"], f"{label}.side_effect_count")
    for event in _arr(result["sdk_events"], f"{label}.sdk_events", 32):
        item = _obj(event, {"event", "trace_code"}, f"{label}.sdk_event")
        event_name = _enum(item["event"], set(SDK_EVENT_CODES), "SDK event")
        if item["trace_code"] != SDK_EVENT_CODES[event_name]: _fail("SDK event mapping is invalid")
    before, after = _state(result["state_before"], f"{label}.state_before"), _state(result["state_after"], f"{label}.state_after")
    if after["side_effect_count"] - before["side_effect_count"] != result["side_effect_count"]: _fail(f"{label} side-effect transition is invalid")
    if not result["required"] and (expected != "NOT_APPLICABLE" or result["trace_codes"] or calls or result["sdk_events"] or result["side_effect_count"] or terminal != {"kind": "not_applicable", "count": 0} or before != {"lifecycle": "fresh", "approval": "not_required", "tool": "none", "resume": "absent", "billing": "not_applicable", "side_effect_count": 0, "boundary_sha256": None} or after != before):
        _fail(f"{label} non-required declaration is not empty")
    if result["required"] and expected == "NOT_APPLICABLE": _fail(f"{label} required path cannot be NOT_APPLICABLE")
    if result["required"] and expected == "PASS" and terminal != {"kind": "complete", "count": 1}: _fail(f"{label} positive declaration is not complete")
    if result["required"] and expected == "EXPECTED_NEGATIVE" and terminal["kind"] not in {"failed", "cancelled"}: _fail(f"{label} denial declaration is not failed/cancelled")
    return result

def _candidate(value: Any) -> dict[str, Any]:
    keys = {"candidate_schema_version", "plugin_sha", "host_sha", "wheel_sha256", "sdk_distribution", "sdk_version", "profile_sha256", "runner_id", "runner_version", "candidate_sha256"}
    result = _obj(value, keys, "candidate")
    if result["candidate_schema_version"] != 1 or result["sdk_distribution"] != "claude-agent-sdk" or result["sdk_version"] != "0.2.144": _fail("candidate envelope is invalid")
    _sha1(result["plugin_sha"], "plugin_sha"); _sha1(result["host_sha"], "host_sha")
    _sha(result["wheel_sha256"], "wheel_sha256"); _sha(result["profile_sha256"], "profile_sha256")
    if result["runner_id"] != "hermes-parity-v3": _fail("runner_id is invalid")
    _version(result["runner_version"], "runner_version")
    identity = {key: result[key] for key in ("plugin_sha", "host_sha", "wheel_sha256", "sdk_distribution", "sdk_version", "profile_sha256", "runner_id", "runner_version")}
    if result["candidate_sha256"] != canonical_sha256(identity): _fail("candidate_sha256 does not match")
    return result

def _resume(value: Any, label: str = "resume") -> dict[str, Any]:
    keys = {"present", "runtime_id", "runtime_schema_version", "state_length", "prior_state_sha256", "supplied_state_sha256", "produced_state_sha256", "accepted", "fixture_ref"}
    result = _obj(value, keys, label)
    _bool(result["present"], f"{label}.present")
    if result["runtime_id"] != "hermes-claude-agent-sdk" or result["runtime_schema_version"] != 1: _fail(f"{label} runtime envelope is invalid")
    _int(result["state_length"], f"{label}.state_length", 0, 512); _bool(result["accepted"], f"{label}.accepted")
    for key in ("prior_state_sha256", "supplied_state_sha256", "produced_state_sha256"):
        _sha(result[key], f"{label}.{key}", True)
    ref = result["fixture_ref"]
    if result["present"] and not 1 <= result["state_length"] <= 512: _fail(f"{label} present state has invalid length")
    if ref == "fixture:none":
        if result["present"] or result["state_length"] or any(result[key] is not None for key in ("prior_state_sha256", "supplied_state_sha256", "produced_state_sha256")): _fail(f"{label} absent state has metadata")
    elif isinstance(ref, str) and ref.startswith("fixture:"): _id(ref[8:], f"{label}.fixture_ref")
    else:
        _fail(f"{label}.fixture_ref is invalid")
    return result

def _billing(value: Any) -> dict[str, Any]:
    result = _obj(value, {"mode", "status", "safe"}, "billing")
    _enum(result["mode"], {"subscription_included", "api_key", "metered", "extra_usage", "glm", "unknown", "not_applicable"}, "billing.mode")
    _enum(result["status"], {"included", "blocked", "reported", "unknown", "not_applicable"}, "billing.status"); _bool(result["safe"], "billing.safe")
    derived = result["mode"] in {"subscription_included", "not_applicable"} and result["status"] in {"included", "not_applicable"}
    if result["safe"] != derived:
        _fail("billing.safe is not derived")
    return result

def _resume_boundary(value: Any) -> None:
    result = _obj(value, {"turn_index", "runtime_id", "runtime_schema_version", "prior_state_sha256", "supplied_state_sha256", "produced_state_sha256", "accepted"}, "run.resume_boundary")
    _int(result["turn_index"], "resume turn", 1); _id(result["runtime_id"], "resume runtime"); _int(result["runtime_schema_version"], "resume schema", 1, 1); _bool(result["accepted"], "resume accepted")
    for key in ("prior_state_sha256", "supplied_state_sha256", "produced_state_sha256"): _sha(result[key], f"resume.{key}", True)

def _inventory(value: Any) -> dict[str, Any]:
    keys = {"candidate_sha256", "declared_inventory_sha256", "tools", "mcp_servers", "observed_inventory_sha256", "unknown_names", "missing_names", "schema_drift_names"}
    result = _obj(value, keys, "inventory")
    _sha(result["candidate_sha256"], "inventory.candidate_sha256"); _sha(result["declared_inventory_sha256"], "inventory.declared_inventory_sha256")
    names: list[str] = []
    for group in ("tools", "mcp_servers"):
        previous = ""
        for entry in _arr(result[group], f"inventory.{group}"):
            item = _obj(entry, {"name", "schema_sha256", "enabled"}, f"inventory.{group}"); name = _id(item["name"], "inventory.name")
            if name <= previous: _fail("inventory group is not sorted/unique")
            previous = name; names.append(name); _sha(item["schema_sha256"], "inventory.schema_sha256"); _bool(item["enabled"], "inventory.enabled")
    if len(names) != len(set(names)): _fail("inventory names overlap")
    for key in ("unknown_names", "missing_names", "schema_drift_names"):
        values = _arr(result[key], f"inventory.{key}"); [_id(item, f"inventory.{key}") for item in values]; _sorted(values, f"inventory.{key}")
    if result["observed_inventory_sha256"] != hash_projection("observed_inventory_sha256", result): _fail("observed inventory hash does not match")
    return result

def _source_rows(value: Any, label: str) -> list[dict[str, str]]:
    rows = _arr(value, label); result = [{"pack_id": _id(_obj(row, {"pack_id", "row_id"}, label)["pack_id"], "pack_id"), "row_id": _id(row["row_id"], "row_id")} for row in rows]
    if result != sorted(result, key=lambda item: (item["pack_id"], item["row_id"])) or len({(item["pack_id"], item["row_id"]) for item in result}) != len(result): _fail(f"{label} is not sorted/unique")
    return result

def _attempt(value: Any, expected_paths: Mapping[str, Any] | None, label: str, expected_trace: Sequence[str] | None = None) -> dict[str, Any]:
    keys = {"ordinal", "candidate_sha256", "execution_ref_sha256", "boundary_sha256", "fresh_boundary", "trace", "paths", "path_status", "resume", "billing"}
    result = _obj(value, keys, label); _int(result["ordinal"], f"{label}.ordinal", 1); _bool(result["fresh_boundary"], f"{label}.fresh_boundary")
    for key in ("candidate_sha256", "execution_ref_sha256", "boundary_sha256"): _sha(result[key], f"{label}.{key}")
    trace = _arr(result["trace"], f"{label}.trace", 256); codes: list[str] = []
    for seq, entry in enumerate(trace, 1):
        item = _obj(entry, {"seq", "code", "actor", "phase", "correlation_sha256"}, f"{label}.trace"); code = item["code"]
        if item["seq"] != seq or code not in TRACE_REGISTRY or (item["actor"], item["phase"]) != TRACE_REGISTRY[code]: _fail(f"{label}.trace is invalid")
        _sha(item["correlation_sha256"], "trace.correlation_sha256", True); codes.append(code)
    if expected_trace is not None and codes != list(expected_trace): _fail(f"{label}.trace differs from catalog")
    paths, statuses = _obj(result["paths"], set(PATHS), f"{label}.paths"), _obj(result["path_status"], set(PATHS), f"{label}.path_status")
    for name in PATHS:
        actual = _path(paths[name], f"{label}.paths.{name}"); expected = _path(expected_paths[name], f"{label}.expected.{name}") if expected_paths else actual
        if expected_paths and actual != expected: _fail(f"{label}.paths.{name} differs from catalog")
        status = validate_path_status(statuses[name], expected["expected_outcome"], f"{label}.path_status.{name}")
        if status["status"] == "NOT_APPLICABLE" and expected["required"]: _fail(f"{label}.required path is NOT_APPLICABLE")
        if not expected["required"] and status["status"] != "NOT_APPLICABLE": _fail(f"{label}.non-required path executed")
        if expected_paths and status["status"] in {"PASS", "EXPECTED_NEGATIVE"} and status["observed_trace_codes"] != expected["trace_codes"]:
            _fail(f"{label}.path_status.{name} trace differs from catalog")
        if status["status"] in {"PASS", "EXPECTED_NEGATIVE", "VERIFIED_FAILURE"}:
            begin, end = f"path.{name}.begin", f"path.{name}.end"
            if codes.count(begin) != 1 or codes.count(end) != 1: _fail(f"{label}.{name} lacks one contiguous boundary")
            start, stop = codes.index(begin), codes.index(end)
            if stop <= start or codes[start + 1:stop] != status["observed_trace_codes"]: _fail(f"{label}.{name} trace is not contiguous")
    observed_terminals = sorted(code for name in PATHS for code in statuses[name]["observed_trace_codes"] if code.startswith("terminal."))
    trace_terminals = sorted(item["code"] for item in trace if item["code"].startswith("terminal."))
    if observed_terminals != trace_terminals: _fail(f"{label} terminal trace count is not exact")
    _resume(result["resume"], f"{label}.resume"); _billing(result["billing"])
    return result

def _sdk_grade(value: Any, catalog: Mapping[str, Any]) -> dict[str, Any]:
    result = _obj(value, {"ledger_sha256", "row_count", "requires_0_3_239_rows", "upgrade_issue_ref", "status"}, "grade.sdk_ledger")
    ledger = catalog.get("sdk_ledger") if isinstance(catalog, Mapping) else None
    packs = catalog.get("source_packs") if isinstance(catalog, Mapping) else None
    source = next((pack for pack in packs or [] if isinstance(pack, Mapping) and pack.get("id") == "sdk_boundary"), None)
    if not isinstance(ledger, Mapping) or not isinstance(source, Mapping): _fail("catalog SDK ledger is unavailable")
    keys = [("sdk_boundary", row["row_id"] if isinstance(row, Mapping) else row) for row in source.get("row_ids", [])]
    ledger = validate_sdk_ledger(ledger, keys)
    rows = sorted(row["row_id"] for row in ledger["rows"] if row["classification"] == "requires_0_3_239")
    derived = {"ledger_sha256": ledger["ledger_sha256"], "row_count": 23, "requires_0_3_239_rows": rows, "upgrade_issue_ref": "issue:16", "status": "STOP" if rows else "CLEAR"}
    if result != derived: _fail("grade.sdk_ledger is not bound to catalog")
    return result

def _grade(value: Any, cells: list[Mapping[str, Any]], statuses: list[str], inventory: Mapping[str, Any], billing_safe: bool, isolation_safe: bool, catalog: Mapping[str, Any], records: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]], terminal_exact: bool = True, resume_safe: bool = True, bijection: bool = True) -> dict[str, Any]:
    result = _obj(value, GRADE_KEYS, "result.grade")
    qualifications = {cell["capability_id"]: {name: attempts[-1]["path_status"][name]["qualification"] for name in PATHS} for cell, attempts in records}
    caret = {cell["capability_id"]: len(attempts) == 3 and all(all(attempt["path_status"][name]["qualification"] in {"PASS", "EXPECTED_NEGATIVE", "NOT_APPLICABLE"} for name in PATHS) for attempt in attempts) for cell, attempts in records}
    at = {cell["capability_id"]: (not isinstance(next((cap for cap in catalog.get("capabilities", []) if cap.get("id") == cell["capability_id"]), {}), Mapping) or next((cap for cap in catalog.get("capabilities", []) if cap.get("id") == cell["capability_id"]), {}).get("repeat_policy", {}).get("mode") != "consecutive_3" or len(attempts) == 3) and any(all(attempt["path_status"][name]["qualification"] in {"PASS", "EXPECTED_NEGATIVE", "NOT_APPLICABLE"} for name in PATHS) for attempt in attempts) for cell, attempts in records}
    qualifications = {cell["capability_id"]: dict(qualifications[cell["capability_id"]], qualified=at[cell["capability_id"]], not_required_paths=sorted(name for name in PATHS if not attempts[-1]["paths"][name]["required"]), attempts=len(attempts)) for cell, attempts in records}
    status = "BLOCKED" if not cells or any("BLOCKED" in item.values() for item in qualifications.values()) else "PENDING" if any("PENDING" in item.values() for item in qualifications.values()) else "FAIL" if any(qual == "FAIL" for item in qualifications.values() for qual in item.values()) or any(inventory[key] for key in ("unknown_names", "missing_names", "schema_drift_names")) or not terminal_exact or not billing_safe or not isolation_safe or not resume_safe or not bijection else "PASS"
    for key in ("required_cell_count", "observed_cell_count", "required_pass_count", "observed_pass_count", "expected_negative_count", "verified_failure_count", "environment_blocked_count", "pending_count"): _int(result[key], f"grade.{key}")
    if result["required_cell_count"] != sum(1 for cell in cells if cell["required"]) or result["observed_cell_count"] != len(cells) or result["required_pass_count"] != statuses.count("PASS") or result["observed_pass_count"] != statuses.count("PASS") or result["expected_negative_count"] != statuses.count("EXPECTED_NEGATIVE") or result["verified_failure_count"] != statuses.count("VERIFIED_FAILURE") or result["environment_blocked_count"] != statuses.count("ENVIRONMENT_BLOCKED") or result["pending_count"] != statuses.count("PENDING"): _fail("grade counts are not derived")
    for key, expected in (("cell_qualifications", qualifications), ("pass_caret_3", caret), ("pass_at_3", at)):
        _derived(result[key], f"grade.{key}")
        if result[key] != expected: _fail("grade qualification projections are not derived")
    sdk = _sdk_grade(result["sdk_ledger"], catalog)
    if sdk["status"] == "STOP" and status == "PASS": status = "FAIL"
    for key in ("candidate_consistent", "terminal_events_exact", "inventory_exact", "billing_safe", "resume_safe", "isolation_safe", "result_bijection"): _bool(result[key], f"grade.{key}")
    derived = {"candidate_consistent": True, "terminal_events_exact": terminal_exact, "inventory_exact": not any(inventory[key] for key in ("unknown_names", "missing_names", "schema_drift_names")), "billing_safe": billing_safe, "resume_safe": resume_safe, "isolation_safe": isolation_safe, "result_bijection": bijection}
    if result["status"] != status or any(result[key] != expected for key, expected in derived.items()): _fail("grade.status is not derived" if result["status"] != status else "grade derived field is not recomputed")
    return result

def validate_sdk_ledger(value: Any, source_keys: Sequence[tuple[str, str]] | None = None) -> dict[str, Any]:
    result = _obj(value, {"schema_version", "rows", "rows_sha256", "ledger_sha256"}, "sdk_ledger")
    if result["schema_version"] != 1: _fail("SDK ledger schema version is invalid")
    rows = _arr(result["rows"], "sdk_ledger.rows", 23); seen: list[tuple[str, str]] = []
    for ordinal, row in enumerate(rows, 1):
        item = _obj(row, {"pack_id", "row_id", "ordinal", "executable", "classification", "proof"}, "sdk_ledger.row")
        if item["pack_id"] != "sdk_boundary" or item["ordinal"] != ordinal: _fail("SDK ledger ordinal/key is invalid")
        key = (item["pack_id"], _id(item["row_id"], "sdk row_id"));
        if key in seen: _fail("SDK ledger row keys are duplicated")
        seen.append(key); _bool(item["executable"], "sdk executable"); _enum(item["classification"], {"covered_current", "equivalent_host", "requires_0_3_239", "not_runtime_applicable"}, "sdk classification")
        if item["executable"] and item["classification"] == "not_runtime_applicable": _fail("executable SDK row is N/A")
        proof = _obj(item["proof"], {"ref", "sha256"}, "sdk proof"); _id(proof["ref"], "sdk proof ref"); _sha(proof["sha256"], "sdk proof hash")
    if len(rows) != 23 or source_keys is not None and sorted(seen) != sorted(source_keys): _fail("SDK ledger is not the exact 23-row source set")
    if result["rows_sha256"] != hash_projection("rows_sha256", result) or result["ledger_sha256"] != hash_projection("ledger_sha256", result): _fail("SDK ledger hash mismatch")
    return result

def validate_freeze(value: Any) -> dict[str, Any]:
    keys = {"freeze_schema_version", "contract_id", "contract_sha256", "catalog_sha256", "source_map_sha256", "receipt_artifact_sha256", "replacement_receipt_sha256", "fixture_manifest_sha256", "candidate_sha256", "declared_inventory_sha256", "observed_inventory_sha256", "sdk_ledger_sha256", "scenario_sha256", "scope_partition_id", "session_scope", "capability_set_sha256", "frozen", "freeze_sha256"}
    result = _obj(value, keys, "freeze")
    if result["freeze_schema_version"] != 1 or result["contract_id"] != "hermes-agent-sdk-feature-parity": _fail("freeze envelope is invalid")
    for key in keys - {"freeze_schema_version", "contract_id", "scope_partition_id", "session_scope", "frozen", "freeze_sha256"}: _sha(result[key], f"freeze.{key}")
    _pref(result["scope_partition_id"], "PART-", "freeze.scope_partition_id"); _enum(result["session_scope"], SCOPES, "freeze.session_scope"); _bool(result["frozen"], "freeze.frozen")
    if not result["frozen"] or result["freeze_sha256"] != canonical_sha256(_omit(result, "freeze_sha256")): _fail("freeze hash is invalid")
    return result

def validate_result(value: Any, *, freeze: Mapping[str, Any] | None = None, catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    keys = {"result_schema_version", "freeze_sha256", "contract_sha256", "catalog_sha256", "source_map_sha256", "fixture_manifest_sha256", "result_sha256", "candidate", "inventory", "run", "cells", "grade", "redaction"}
    result = _obj(value, keys, "result")
    if result["result_schema_version"] != "3.0.0": _fail("result schema version is invalid")
    for key in ("freeze_sha256", "contract_sha256", "catalog_sha256", "source_map_sha256", "fixture_manifest_sha256"): _sha(result[key], f"result.{key}")
    if not isinstance(catalog, Mapping): _fail("catalog is required to bind SDK ledger and cells")
    _safe(catalog)
    candidate, inventory = _candidate(result["candidate"]), _inventory(result["inventory"])
    run = _obj(result["run"], {"run_id_sha256", "scenario_sha256", "mode", "scope_partition_id", "session_scope", "candidate_unchanged", "cell_count", "required_cell_count", "terminal_event_count", "resume_boundary"}, "result.run")
    _sha(run["run_id_sha256"], "run_id_sha256"); _sha(run["scenario_sha256"], "scenario_sha256"); _enum(run["mode"], {"deterministic", "integration", "live"}, "run.mode"); _pref(run["scope_partition_id"], "PART-", "run.scope_partition_id"); _enum(run["session_scope"], SCOPES, "run.session_scope"); _bool(run["candidate_unchanged"], "run.candidate_unchanged")
    if not run["candidate_unchanged"]: _fail("run candidate_unchanged must be true")
    _int(run["cell_count"], "run.cell_count"); _int(run["required_cell_count"], "run.required_cell_count"); _int(run["terminal_event_count"], "run.terminal_event_count")
    if run["resume_boundary"] is not None: _resume_boundary(run["resume_boundary"])
    cells = _arr(result["cells"], "result.cells"); ids: list[str] = []; statuses: list[str] = []; records: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = []; boundaries: set[str] = set(); billing_safe = True; isolation_safe = True; resume_safe = True; catalog_caps = {cap["id"]: cap for cap in catalog.get("capabilities", [])}
    for index, cell in enumerate(cells, 1):
        item = _obj(cell, {"capability_id", "scenario_id", "source_rows", "session_scope", "attempts", "required"}, f"cell[{index}]"); cap_id = _id(item["capability_id"], "capability_id")
        if cap_id in ids: _fail("result capability IDs are duplicated")
        _pref(cap_id, "CAP-", "capability_id"); ids.append(cap_id); _pref(item["scenario_id"], "SCN-", "scenario_id"); _source_rows(item["source_rows"], "cell.source_rows"); _enum(item["session_scope"], SCOPES, "cell.session_scope"); _bool(item["required"], "cell.required")
        if item["session_scope"] != run["session_scope"]: _fail("cell scope differs from run")
        attempts = _arr(item["attempts"], "cell.attempts", 3); cap = catalog_caps.get(cap_id); expected_paths = None
        if catalog is not None:
            if cap is None or item["scenario_id"] != cap["scenario_id"] or item["source_rows"] != cap["source_rows"] or item["required"] != cap["required"]: _fail("cell does not copy catalog")
            expected_paths = {name: cap[f"{name}_path"] for name in PATHS}
        if cap is not None and cap.get("repeat_policy", {"mode": "once"}).get("mode") == "once" and len(attempts) != 1: _fail("once cell must have one attempt")
        if cap is not None and cap.get("repeat_policy", {"mode": "once"}).get("mode") == "consecutive_3" and not 1 <= len(attempts) <= 3: _fail("repeat cell has invalid attempt count")
        previous = None; validated_attempts: list[Mapping[str, Any]] = []
        for ordinal, attempt in enumerate(attempts, 1):
            if not isinstance(attempt, Mapping) or attempt.get("ordinal") != ordinal: _fail("attempt ordinals are not contiguous")
            validated = _attempt(attempt, expected_paths, f"cell[{index}].attempt[{ordinal}]", cap.get("expected_trace"))
            if validated["candidate_sha256"] != candidate["candidate_sha256"]: _fail("attempt candidate differs")
            if item["session_scope"] == "isolated_cell" and (not validated["fresh_boundary"] or validated["boundary_sha256"] in boundaries): isolation_safe = False; _fail("isolated boundary is not fresh/unique")
            boundaries.add(validated["boundary_sha256"]); resume = validated["resume"]
            if previous is not None and resume["prior_state_sha256"] != previous: resume_safe = False; _fail("resume prior state does not chain")
            previous = resume["produced_state_sha256"]; billing_safe = billing_safe and validated["billing"]["safe"]
            statuses.extend(validated["path_status"][name]["status"] for name in PATHS); validated_attempts.append(validated)
        records.append((item, validated_attempts))
    terminal_total = sum(1 for status in statuses if status in {"PASS", "EXPECTED_NEGATIVE", "VERIFIED_FAILURE"})
    if set(ids) != set(catalog_caps): _fail("result cells are not a catalog bijection")
    terminal_exact = run["terminal_event_count"] == terminal_total and run["cell_count"] == len(cells) and run["required_cell_count"] == sum(1 for cell in cells if cell["required"])
    if not terminal_exact: _fail("run derived counts are invalid")
    if freeze is None: _fail("freeze is required to bind result")
    _grade(result["grade"], cells, statuses, inventory, billing_safe, isolation_safe, catalog, records, terminal_exact, resume_safe, True); validate_redaction(result["redaction"])
    if result["result_sha256"] != canonical_sha256(_omit(result, "result_sha256")): _fail("result hash is invalid")
    if freeze is not None:
        frozen = validate_freeze(freeze)
        if frozen["capability_set_sha256"] != canonical_sha256(sorted(ids)) or result["freeze_sha256"] != frozen["freeze_sha256"] or candidate["candidate_sha256"] != frozen["candidate_sha256"] or any(result[key] != frozen[key] for key in ("contract_sha256", "catalog_sha256", "source_map_sha256", "fixture_manifest_sha256")) or inventory["declared_inventory_sha256"] != frozen["declared_inventory_sha256"] or inventory["observed_inventory_sha256"] != frozen["observed_inventory_sha256"] or run["scenario_sha256"] != frozen["scenario_sha256"] or run["scope_partition_id"] != frozen["scope_partition_id"] or run["session_scope"] != frozen["session_scope"] or frozen["sdk_ledger_sha256"] != catalog["sdk_ledger"]["ledger_sha256"]: _fail("capability_set_sha256 is not bound" if frozen["capability_set_sha256"] != canonical_sha256(sorted(ids)) else "result freeze/candidate binding is invalid")
    if catalog is not None and result["catalog_sha256"] != catalog.get("catalog_sha256"): _fail("result catalog binding is invalid")
    return result

def _aggregate_projection(value: Mapping[str, Any], full: bool = False) -> Any:
    if full:
        return {"source_row_set_sha256": value["source_row_set_sha256"], "partitions": sorted(({"partition_id": packet["partition_id"], "freeze_sha256": packet["freeze_packet"]["freeze_sha256"], "result_sha256": packet["result_packet"]["result_sha256"], "capability_ids": sorted(cell["capability_id"] for cell in packet["result_packet"]["cells"]), "source_rows": sorted((row for cell in packet["result_packet"]["cells"] for row in cell["source_rows"]), key=lambda row: (row["pack_id"], row["row_id"]))} for packet in value["partition_packets"]), key=lambda item: item["partition_id"])}
    return _omit(value, "full_result_sha256", "aggregate_sha256")

def validate_aggregate(value: Any, *, catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    keys = {"aggregate_schema_version", "catalog_sha256", "source_map_sha256", "contract_sha256", "candidate_sha256", "declared_inventory_sha256", "sdk_ledger_sha256", "partition_packets", "source_row_set_sha256", "full_result_sha256", "aggregate_sha256"}
    result = _obj(value, keys, "aggregate")
    if result["aggregate_schema_version"] != 1: _fail("aggregate schema version is invalid")
    if not isinstance(catalog, Mapping): _fail("catalog is required for aggregate validation")
    for key in keys - {"aggregate_schema_version", "partition_packets", "source_row_set_sha256", "full_result_sha256", "aggregate_sha256"}: _sha(result[key], f"aggregate.{key}")
    packets = _arr(result["partition_packets"], "aggregate.partition_packets"); seen: set[str] = set(); all_caps: set[str] = set(); rows: list[dict[str, str]] = []; boundaries: set[str] = set()
    if catalog is not None and isinstance(catalog.get("sdk_ledger"), Mapping): validate_sdk_ledger(catalog["sdk_ledger"], [("sdk_boundary", row) for pack in catalog.get("source_packs", []) if pack.get("id") == "sdk_boundary" for row in pack.get("row_ids", [])])
    if not packets: _fail("aggregate must contain packets")
    for packet in packets:
        item = _obj(packet, {"partition_id", "freeze_packet", "result_packet"}, "aggregate.packet"); partition_id = _pref(item["partition_id"], "PART-", "partition_id")
        if partition_id in seen: _fail("aggregate partition IDs overlap")
        seen.add(partition_id); freeze = validate_freeze(item["freeze_packet"]); partition = validate_result(item["result_packet"], freeze=freeze, catalog=catalog)
        if partition_id != freeze["scope_partition_id"] or partition_id != partition["run"]["scope_partition_id"]: _fail("aggregate partition binding is invalid")
        for cell in partition["cells"]:
            if cell["capability_id"] in all_caps: _fail("aggregate capability IDs overlap")
            all_caps.add(cell["capability_id"]); rows.extend(cell["source_rows"])
            for attempt in cell["attempts"]:
                if cell["session_scope"] == "isolated_cell" and (not attempt["fresh_boundary"] or attempt["boundary_sha256"] in boundaries): _fail("aggregate isolated boundary is not fresh/unique")
                boundaries.add(attempt["boundary_sha256"])
        for key in ("candidate_sha256", "catalog_sha256", "source_map_sha256", "contract_sha256", "declared_inventory_sha256", "sdk_ledger_sha256"):
            if (freeze[key] if key in freeze else partition[key]) != result[key]: _fail(f"aggregate {key} binding is invalid")
    if packets != sorted(packets, key=lambda packet: packet["partition_id"]): _fail("aggregate packets are not sorted")
    if all_caps != {cap["id"] for cap in catalog.get("capabilities", [])}: _fail("aggregate capabilities are not a catalog bijection")
    rows = sorted(rows, key=lambda row: (row["pack_id"], row["row_id"]))
    if len({(row["pack_id"], row["row_id"]) for row in rows}) != len(rows): _fail("aggregate source rows overlap")
    if result["source_row_set_sha256"] != canonical_sha256(rows) or result["full_result_sha256"] != canonical_sha256(_aggregate_projection(result, True)) or result["aggregate_sha256"] != canonical_sha256(_aggregate_projection(result)): _fail("aggregate hash projection is invalid")
    if result["catalog_sha256"] != catalog.get("catalog_sha256"): _fail("aggregate catalog binding is invalid")
    return result

def hash_projection(field: str, value: Mapping[str, Any] | Sequence[Any]) -> str:
    if field == "candidate_sha256":
        source = _map(value, "candidate"); return canonical_sha256({key: source[key] for key in ("plugin_sha", "host_sha", "wheel_sha256", "sdk_distribution", "sdk_version", "profile_sha256", "runner_id", "runner_version")})
    if field == "row_ids_sha256": return canonical_sha256(sorted(_map(value, "source pack")["row_ids"]))
    if field == "source_row_set_sha256": return canonical_sha256(sorted(value, key=lambda row: (row["pack_id"], row["row_id"])))
    if field == "source_map_sha256":
        source = _map(value, "catalog"); packs = source.get("source_packs", source); return canonical_sha256([{key: item[key] for key in ("id", "expected_count", "row_ids", "source_ref", "source_identity", "provenance", "license", "attribution") if key in item} for item in sorted(packs, key=lambda item: item["id"])])
    if field == "rows_sha256":
        ledger = _map(value, "ledger"); return canonical_sha256(sorted([{key: row[key] for key in ("pack_id", "row_id", "ordinal", "executable", "classification", "proof")} for row in ledger["rows"]], key=lambda row: (row["pack_id"], row["row_id"])))
    if field == "ledger_sha256":
        ledger = _map(value, "ledger"); return canonical_sha256({key: ledger[key] for key in ("schema_version", "rows")})
    if field == "declared_inventory_sha256":
        source = _map(value, "inventory"); return canonical_sha256({key: source[key] for key in ("tools", "mcp_servers")})
    if field == "observed_inventory_sha256": return canonical_sha256(_omit(_map(value, "inventory"), "declared_inventory_sha256", "observed_inventory_sha256", "unknown_names", "missing_names", "schema_drift_names"))
    if field == "capability_set_sha256":
        source = _map(value, "capability set"); ids = source.get("capability_ids", source.get("ids", source.get("cells", []))); return canonical_sha256(sorted(item["capability_id"] if isinstance(item, Mapping) else item for item in ids))
    if field == "full_result_sha256": return canonical_sha256(_aggregate_projection(_map(value, "aggregate"), True))
    if field == "aggregate_sha256": return canonical_sha256(_aggregate_projection(_map(value, "aggregate")))
    if field == "replacement_receipt_sha256": return canonical_sha256(_omit(_map(value, "replacement receipt"), "replacement_receipt_sha256"))
    if field == "fixture_manifest_sha256":
        source = _map(value, "fixture manifest"); return canonical_sha256({"fixtures": [{key: item[key] for key in ("ref", "kind", "content_sha256", "byte_length")} for item in sorted(source["fixtures"], key=lambda item: item["ref"])]}) if "fixtures" in source else canonical_sha256(_omit(source, field))
    if field == "scenario_sha256":
        source = _map(value, "scenario"); caps = sorted(source["capabilities"], key=lambda item: item.get("capability_id", "")) if isinstance(source.get("capabilities"), list) else source.get("capabilities"); return canonical_sha256({key: (caps if key == "capabilities" else source[key]) for key in ("scenario_schema_version", "catalog_sha256", "fixture_manifest_sha256", "scope_partition_id", "capabilities") if key in source})
    if field in {"resume_sha256", "catalog_sha256", "freeze_sha256", "result_sha256"}: return canonical_sha256(_omit(_map(value, field), field))
    _fail(f"no packet projection is defined for {field}")

__all__ = ["OBSERVED", "QUALIFICATIONS", "REDACTION_CATEGORIES", "PacketValidationError", "derive_path_qualification", "hash_projection", "validate_aggregate", "validate_freeze", "validate_path_status", "validate_redaction", "validate_result", "validate_sdk_ledger"]

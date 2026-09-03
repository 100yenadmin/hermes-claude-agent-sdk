"""Read-only, provider-free evidence collection from an isolated Hermes DB.

Only hashes, counts, classifications, and bounded model/usage metadata cross
this boundary.  The collector intentionally does not import Hermes internals:
the generic tables are a small host-owned compatibility surface and SQLite is
opened with ``mode=ro`` plus ``query_only``.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .hashing import sha256_value

V4_HOST_PROBE_SCHEMA_VERSION = 1
HOST_RUNTIME_ID = "hermes-claude-agent-sdk"
V4_MODEL = "claude-fable-5-1"
V4_RECEIPT_PROVIDER = "anthropic"
_MAX_TOKEN = 10**12
_MAX_STATE_BYTES = 64 * 1024
_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,512}$")
_RAW_FIELDS = frozenset({
    "raw", "content", "prompt", "transcript", "message", "messages",
    "session", "session_id", "correlation_id", "tool_call_id", "tool_calls",
    "tool_result", "tool_results", "arguments", "result", "secret", "token",
})
_TABLE_COLUMNS = {
    "sessions": ("id", "api_call_count"),
    "messages": ("id", "session_id", "role", "content", "tool_call_id", "tool_calls", "tool_name", "timestamp", "finish_reason", "active"),
    "runtime_session_state": ("session_id", "runtime_id", "schema_version", "state_json", "updated_at"),
    "runtime_usage_receipts": ("id", "session_id", "runtime_id", "provider", "model", "selected_model", "effective_model", "canonical_model", "model_resolution", "billing_mode", "cost_status", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "replay_safe", "correlation_id", "fallback_used", "failure_phase", "recorded_at"),
}
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


class V4HostProbeViolation(ValueError):
    """The supplied isolated host evidence is absent, ambiguous, or unsafe."""


def _bad(message: str) -> None:
    raise V4HostProbeViolation(message)


def _session(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or not _TEXT.fullmatch(value):
        _bad("session evidence is not a bounded opaque identity")
    return value


def _path(db_path: Any, allowed_root: Any) -> Path:
    try:
        root_input, db_input = Path(allowed_root), Path(db_path)
        if root_input.is_symlink() or not root_input.is_dir():
            _bad("database isolation root is unavailable")
        root = root_input.resolve(strict=True)
        if db_input.is_symlink() or not db_input.is_file():
            _bad("database path is not an isolated regular file")
        database = db_input.resolve(strict=True)
        database.relative_to(root)
        return database
    except V4HostProbeViolation:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        _bad("database path is not an isolated regular file")


def _raw_request(*, requested_fields: Any, fields: Any, raw_fields: Any, include_raw: Any) -> None:
    if include_raw is True:
        _bad("raw host fields are not permitted")
    supplied = [value for value in (requested_fields, fields, raw_fields) if value is not None]
    if fields is not None and requested_fields is not None and fields != requested_fields:
        _bad("requested host fields are ambiguous")
    for value in supplied:
        if isinstance(value, str):
            value = (value,)
        if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
            _bad("requested host fields are invalid")
        for field in value:
            if not isinstance(field, str):
                _bad("requested host fields are invalid")
            lowered = field.casefold().replace("-", "_")
            if lowered in _RAW_FIELDS or lowered.startswith("raw_") or lowered.endswith("_raw") or "content" in lowered or "prompt" in lowered or "transcript" in lowered:
                _bad("raw host fields are not permitted")


def _connect(database: Path) -> sqlite3.Connection:
    try:
        uri = f"file:{quote(str(database), safe='/')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            conn.close()
            _bad("host database is not query-only")
        return conn
    except V4HostProbeViolation:
        raise
    except (OSError, sqlite3.Error):
        _bad("host database cannot be opened read-only")


def _schema(conn: sqlite3.Connection) -> None:
    try:
        for table, required in _TABLE_COLUMNS.items():
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
                _bad("required host table is missing")
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            if not set(required) <= columns:
                _bad("required host columns are missing")
    except V4HostProbeViolation:
        raise
    except sqlite3.Error:
        _bad("host schema cannot be inspected")


def _text_hash(value: Any, field: str) -> str:
    if value is None:
        return sha256_value({"present": False})
    if not isinstance(value, str) or (field not in {"content", "calls"} and not _TEXT.fullmatch(value)):
        _bad("stored host text is malformed")
    return sha256_value({"present": True, "value": value})


def _bounded_int(value: Any, *, allow_zero: bool = True) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1) or value > _MAX_TOKEN:
        _bad("stored host numeric evidence is outside bounds")
    return value


def _tool_calls(raw: Any) -> list[Mapping[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, str) or not raw:
        _bad("stored tool-call evidence is malformed")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        _bad("stored tool-call evidence is malformed")
    if not isinstance(parsed, list) or len(parsed) > 128:
        _bad("stored tool-call evidence is malformed")
    result: list[Mapping[str, Any]] = []
    for item in parsed:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not _TEXT.fullmatch(item["id"]):
            _bad("stored tool-call evidence is malformed")
        function = item.get("function")
        name = function.get("name") if isinstance(function, Mapping) else item.get("name")
        if not isinstance(name, str) or not _TEXT.fullmatch(name):
            _bad("stored tool-call evidence is malformed")
        result.append(item)
    return result


def _transcript(rows: Sequence[sqlite3.Row], expected_turn_count: int) -> dict[str, Any]:
    categories: dict[str, list[Any]] = {name: [] for name in ("user", "assistant", "tool_call", "tool_result")}
    ids: list[int] = []
    calls: dict[str, int] = {}
    results: dict[str, int] = {}
    terminal: list[dict[str, Any]] = []
    roles: list[str] = []
    for ordinal, row in enumerate(rows):
        row_id, role = row["id"], row["role"]
        if type(row_id) is not int or row_id <= 0 or role not in {"user", "assistant", "tool"}:
            _bad("stored transcript row is malformed")
        ids.append(row_id); roles.append(role)
        if len(ids) > 1 and ids[-1] <= ids[-2]:
            _bad("stored transcript ordering is not canonical")
        call_rows = _tool_calls(row["tool_calls"]) if role == "assistant" else []
        if role != "assistant" and row["tool_calls"] is not None:
            _bad("stored transcript row is malformed")
        call_id = row["tool_call_id"]
        if role == "tool":
            if not isinstance(call_id, str) or not _TEXT.fullmatch(call_id):
                _bad("stored tool-result pairing is malformed")
            if call_id in results:
                _bad("stored tool-result pairing is ambiguous")
            results[call_id] = ordinal
        elif call_id is not None:
            _bad("stored transcript row is malformed")
        descriptor = {"ordinal": ordinal, "content_sha256": _text_hash(row["content"], "content"), "tool_call_id_sha256": _text_hash(call_id, "id"), "tool_calls_sha256": _text_hash(row["tool_calls"], "calls"), "tool_name_sha256": _text_hash(row["tool_name"], "name"), "finish_reason_sha256": _text_hash(row["finish_reason"], "finish")}
        if role == "user":
            categories["user"].append(descriptor)
        elif role == "assistant":
            categories["assistant"].append(descriptor)
            for call in call_rows:
                call_id = call["id"]
                if call_id in calls:
                    _bad("stored tool-call pairing is ambiguous")
                calls[call_id] = ordinal
                categories["tool_call"].append({"ordinal": ordinal, "call_sha256": sha256_value(call)})
            finish = row["finish_reason"]
            if not isinstance(finish, (str, type(None))) or (isinstance(finish, str) and not _TEXT.fullmatch(finish)):
                _bad("stored transcript row is malformed")
            if not call_rows and finish not in {"tool_calls", "error", "agent_error", "content_filter"}:
                terminal.append(descriptor)
        else:
            categories["tool_result"].append({"ordinal": ordinal, "result_sha256": descriptor["content_sha256"], "tool_call_id_sha256": descriptor["tool_call_id_sha256"], "tool_name_sha256": descriptor["tool_name_sha256"]})
    if len(rows) == 0 or len(terminal) != expected_turn_count or len(categories["user"]) != expected_turn_count or rows[-1]["role"] != "assistant" or not _tool_calls(rows[-1]["tool_calls"]) == []:
        _bad("persisted assistant terminal evidence is missing or ambiguous")
    if set(calls) != set(results) or any(results[key] <= calls[key] for key in calls):
        _bad("stored tool-call/result pairing is incomplete")
    return {"row_count": len(rows), "canonical_rows": {key: {"count": len(value), "sha256": sha256_value(value)} for key, value in categories.items()}, "ordering": {"key": "id", "direction": "ascending", "strict": True, "sha256": sha256_value({"ids": ids, "roles": roles})}, "terminal": {"count": expected_turn_count, "persisted": True, "sha256": sha256_value(terminal)}}


def _state(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    rows = conn.execute("SELECT schema_version, state_json FROM runtime_session_state WHERE session_id=? AND runtime_id=?", (session_id, HOST_RUNTIME_ID)).fetchall()
    if len(rows) > 1:
        _bad("runtime state evidence is ambiguous")
    if not rows:
        return {"present": False, "schema_version": None, "sha256": None}
    version, encoded = rows[0]["schema_version"], rows[0]["state_json"]
    if type(version) is not int or version <= 0 or version > 1_000_000 or not isinstance(encoded, str) or len(encoded.encode("utf-8")) > _MAX_STATE_BYTES:
        _bad("runtime state evidence is malformed")
    try:
        decoded = json.loads(encoded)
    except (TypeError, ValueError, RecursionError):
        _bad("runtime state evidence is malformed")
    if not isinstance(decoded, dict):
        _bad("runtime state evidence is malformed")
    return {"present": True, "schema_version": version, "sha256": sha256_value({"schema_version": version, "state_json": encoded})}


def _usage(conn: sqlite3.Connection, session: sqlite3.Row, expected_turn_count: int) -> dict[str, Any]:
    rows = conn.execute("SELECT id, provider, model, selected_model, effective_model, canonical_model, model_resolution, billing_mode, cost_status, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, replay_safe, correlation_id, fallback_used, recorded_at FROM runtime_usage_receipts WHERE session_id=? AND runtime_id=? ORDER BY id ASC", (session["id"], HOST_RUNTIME_ID)).fetchall()
    if len(rows) != expected_turn_count:
        _bad("runtime usage receipt evidence is absent or ambiguous")
    projections: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, 1):
        if (row["provider"], row["model"], row["selected_model"], row["effective_model"], row["model_resolution"], row["billing_mode"], row["cost_status"]) != (V4_RECEIPT_PROVIDER, V4_MODEL, V4_MODEL, V4_MODEL, "exact", "subscription_included", "included") or row["canonical_model"] not in (None, V4_MODEL) or row["fallback_used"] != 0 or row["replay_safe"] not in (0, 1):
            _bad("runtime usage receipt model or billing evidence is not exact")
        correlation = row["correlation_id"]
        if not isinstance(correlation, str) or not _TEXT.fullmatch(correlation) or len(correlation.encode("utf-8")) > 2048:
            _bad("runtime usage receipt correlation evidence is malformed")
        tokens = {field: _bounded_int(row[field]) for field in _TOKEN_FIELDS}
        if type(row["recorded_at"]) not in (int, float) or not math.isfinite(float(row["recorded_at"])) or row["recorded_at"] < 0:
            _bad("runtime usage receipt timestamp is malformed")
        correlation_digest = {"sha256": sha256_value(correlation), "byte_length": len(correlation.encode("utf-8"))}
        digest_fields = {field: row[field] for field in ("provider", "model", "selected_model", "effective_model", "canonical_model", "model_resolution", "billing_mode", "cost_status", *(_TOKEN_FIELDS), "replay_safe", "fallback_used", "recorded_at")}; digest_fields.update({"ordinal": ordinal, "correlation": correlation_digest})
        projections.append({"ordinal": ordinal, "sha256": sha256_value(digest_fields), "correlation": correlation_digest, "provider": V4_RECEIPT_PROVIDER, "model": V4_MODEL, "selected_model": V4_MODEL, "effective_model": V4_MODEL, "canonical_model": row["canonical_model"], "model_resolution": "exact", "billing_mode": "subscription_included", "cost_status": "included", "fallback_used": False, "api_call_count": _bounded_int(session["api_call_count"]), "tokens": tokens})
    return {"receipt_count": len(projections), "ordered": projections, "latest": projections[-1]}


def collect_v4_host_observation(db_path: str | Path, session_id: str, *, allowed_root: str | Path | None = None, requested_fields: Sequence[str] | str | None = None, fields: Sequence[str] | str | None = None, raw_fields: Sequence[str] | str | None = None, include_raw: bool = False, runtime_id: str = HOST_RUNTIME_ID, expected_turn_count: int = 1) -> dict[str, Any]:
    """Collect one closed v4 host observation without mutating the database."""
    _raw_request(requested_fields=requested_fields, fields=fields, raw_fields=raw_fields, include_raw=include_raw)
    if type(expected_turn_count) is not int or not 1 <= expected_turn_count <= 4:
        _bad("expected turn count is outside bounds")
    if runtime_id != HOST_RUNTIME_ID:
        _bad("runtime identity is not the Claude runtime")
    sid = _session(session_id)
    database = _path(db_path, allowed_root)
    conn = _connect(database)
    try:
        _schema(conn)
        sessions = conn.execute("SELECT id, api_call_count FROM sessions WHERE id=?", (sid,)).fetchall()
        if len(sessions) != 1:
            _bad("session evidence is absent or ambiguous")
        rows = conn.execute("SELECT id, role, content, tool_call_id, tool_calls, tool_name, timestamp, finish_reason FROM messages WHERE session_id=? AND active=1 ORDER BY id ASC", (sid,)).fetchall()
        return {"schema_version": V4_HOST_PROBE_SCHEMA_VERSION, "status": "PASS", "expected_turn_count": expected_turn_count, "transcript": _transcript(rows, expected_turn_count), "runtime_state": _state(conn, sid), "runtime_usage": _usage(conn, sessions[0], expected_turn_count)}
    except V4HostProbeViolation:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError, UnicodeError, RecursionError):
        _bad("host evidence cannot be read safely")
    finally:
        conn.close()


probe_v4_host = collect_v4_host_observation
collect_v4_host_probe = collect_v4_host_observation
collect_host_ownership_observation = collect_v4_host_observation
collect_host_probe = collect_v4_host_observation
RUNTIME_ID = HOST_RUNTIME_ID
HostProbeViolation = V4HostProbeViolation
V4HostProbeError = V4HostProbeViolation

__all__ = ["HOST_RUNTIME_ID", "HostProbeViolation", "RUNTIME_ID", "V4HostProbeError", "V4HostProbeViolation", "V4_HOST_PROBE_SCHEMA_VERSION", "collect_host_ownership_observation", "collect_host_probe", "collect_v4_host_observation", "collect_v4_host_probe", "probe_v4_host"]

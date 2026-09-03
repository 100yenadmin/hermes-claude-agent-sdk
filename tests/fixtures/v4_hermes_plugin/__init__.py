"""Tiny provider-free Hermes plugin used only by v4 gateway tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

PLUGIN_ID = "v4_hermes_fixture"
TOOL_NAME = "mcp__hermes-fixture__local_state"
TOOLSET = "v4_hermes_fixture"
STATE_FILE = ".hermes_v4_fixture_state.json"
OPERATIONS = ("check", "record")
MAX_ITEM_COUNT = 32
MAX_RECORD_COUNT = 32
_HASH_SIZE = 64

SCHEMA = {
    "name": TOOL_NAME,
    "description": "Check or record bounded synthetic local fixture state.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "task_root": {"type": "string", "minLength": 1, "maxLength": 4096},
            "item_count": {"type": "integer", "minimum": 0, "maximum": MAX_ITEM_COUNT},
            "item_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        "required": ["operation", "task_root", "item_count", "item_hash"],
        "additionalProperties": False,
    },
}


def _invalid() -> ValueError:
    return ValueError("fixture input rejected")


def _task_root(value: Any) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise _invalid()
    root = Path(value)
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _invalid() from None
    if not root.is_absolute() or ".." in root.parts or not resolved.is_dir() or resolved == Path(resolved.anchor):
        raise _invalid()
    if root.is_symlink():
        raise _invalid()
    return resolved


def _normalize(args: Any) -> tuple[str, Path, int, str]:
    if not isinstance(args, Mapping) or set(args) != {"operation", "task_root", "item_count", "item_hash"}:
        raise _invalid()
    operation, count, item_hash = args["operation"], args["item_count"], args["item_hash"]
    if operation not in OPERATIONS or type(count) is not int or not 0 <= count <= MAX_ITEM_COUNT:
        raise _invalid()
    if not isinstance(item_hash, str) or len(item_hash) != _HASH_SIZE or any(char not in "0123456789abcdef" for char in item_hash):
        raise _invalid()
    return operation, _task_root(args["task_root"]), count, item_hash


def _operation_hash(operation: str, count: int, item_hash: str) -> str:
    encoded = json.dumps(
        {"item_count": count, "item_hash": item_hash, "operation": operation},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _read_state(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_symlink():
        raise _invalid()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise _invalid() from None
    if not isinstance(value, dict) or set(value) != {"schema_version", "record_count", "item_count", "item_hash", "operation_hash"}:
        raise _invalid()
    record_count = value["record_count"]
    if value["schema_version"] != 1 or type(record_count) is not int or not 0 <= record_count < MAX_RECORD_COUNT:
        raise _invalid()
    if type(value["item_count"]) is not int or not 0 <= value["item_count"] <= MAX_ITEM_COUNT:
        raise _invalid()
    if not isinstance(value["item_hash"], str) or len(value["item_hash"]) != _HASH_SIZE or any(char not in "0123456789abcdef" for char in value["item_hash"]):
        raise _invalid()
    if not isinstance(value["operation_hash"], str) or len(value["operation_hash"]) != _HASH_SIZE or any(char not in "0123456789abcdef" for char in value["operation_hash"]):
        raise _invalid()
    return record_count


def _write_state(root: Path, count: int, item_count: int, item_hash: str, operation_hash: str) -> None:
    path = root / STATE_FILE
    if path.is_symlink():
        raise _invalid()
    payload = {
        "schema_version": 1,
        "record_count": count,
        "item_count": item_count,
        "item_hash": item_hash,
        "operation_hash": operation_hash,
    }
    try:
        with open(path, "w", encoding="utf-8", opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW, 0o600)) as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
    except (OSError, ValueError):
        raise _invalid() from None


def fixture_tool(args: Mapping[str, Any], **_: Any) -> str:
    """Implement one bounded local operation; host dispatch remains external."""
    operation, root, item_count, item_hash = _normalize(args)
    operation_hash = _operation_hash(operation, item_count, item_hash)
    if operation == "check":
        result = {"ok": True, "operation": operation, "record_count": 0, "item_count": item_count, "item_hash": item_hash, "operation_hash": operation_hash}
    else:
        previous = _read_state(root / STATE_FILE)
        if previous >= MAX_RECORD_COUNT:
            raise _invalid()
        record_count = previous + 1
        _write_state(root, record_count, item_count, item_hash, operation_hash)
        result = {"ok": True, "operation": operation, "record_count": record_count, "item_count": item_count, "item_hash": item_hash, "operation_hash": operation_hash}
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def pre_tool_call(tool_name: str, args: Mapping[str, Any], **_: Any) -> dict[str, str] | None:
    """Request the host approval gate for state mutation, never grant it."""
    if tool_name != TOOL_NAME:
        return None
    try:
        operation, _, _, _ = _normalize(args)
    except ValueError:
        return {"action": "block", "message": "fixture input rejected"}
    if operation == "record":
        return {"action": "approve", "message": "Hermes host approval required for fixture record"}
    return None


def register(ctx: Any) -> None:
    """Register the fixture tool and policy hook through the public host API."""
    ctx.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=SCHEMA,
        handler=fixture_tool,
        is_async=False,
        description=SCHEMA["description"],
    )
    ctx.register_hook("pre_tool_call", pre_tool_call)

"""Isolated host tools for the adapted ClawProBench native slice.

The sandbox never invokes a shell, browser, scheduler, messaging service, or
OpenClaw runtime.  It exposes the source benchmark's tool names over the real
Hermes host-tool bridge while serving deterministic synthetic fixtures.  The
first admitted call is denied once so one turn can prove denial fencing and
recovery without consuming an external side effect.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


MAX_SANDBOX_FILE_BYTES = 64 * 1024
MAX_SANDBOX_FILES = 128

SKILLS_INVENTORY = {
    "workspaceDir": "/sandbox/workspace",
    "managedSkillsDir": "/sandbox/skills",
    "skills": [
        {
            "name": "calendar",
            "eligible": True,
            "bundled": True,
            "missing": {"bins": [], "anyBins": [], "env": [], "config": [], "os": []},
        },
        {
            "name": "tmux",
            "eligible": True,
            "bundled": True,
            "missing": {"bins": [], "anyBins": [], "env": [], "config": [], "os": []},
        },
        {
            "name": "weather",
            "eligible": True,
            "bundled": True,
            "missing": {"bins": [], "anyBins": [], "env": [], "config": [], "os": []},
        },
        {
            "name": "one-password",
            "eligible": False,
            "bundled": True,
            "missing": {"bins": ["op"], "anyBins": [], "env": [], "config": [], "os": []},
        },
        {
            "name": "slack",
            "eligible": False,
            "bundled": True,
            "missing": {"bins": [], "anyBins": [], "env": ["SLACK_BOT_TOKEN"], "config": [], "os": []},
        },
        {
            "name": "zeta",
            "eligible": False,
            "bundled": False,
            "missing": {"bins": ["zeta"], "anyBins": [], "env": [], "config": [], "os": []},
        },
    ],
}

AGENTS_INVENTORY = [
    {
        "id": "main",
        "workspace": "/sandbox/workspace",
        "model": "claude-fable-5",
        "bindings": 0,
        "isDefault": True,
    },
    {
        "id": "bench-helper",
        "workspace": "/sandbox/helper",
        "model": "claude-fable-5",
        "bindings": 0,
        "isDefault": False,
    },
]

SESSIONS_INVENTORY = {
    "path": "/sandbox/sessions.json",
    "count": 2,
    "sessions": [
        {
            "key": "agent:main:main",
            "inputTokens": 255_678,
            "contextTokens": 200_000,
            "model": "claude-fable-5",
        },
        {
            "key": "agent:main:sidecar",
            "inputTokens": 12_000,
            "contextTokens": 200_000,
            "model": "claude-fable-5",
        },
    ],
}

MESSAGE_DRY_RUN = {
    "action": "send",
    "channel": "telegram",
    "dryRun": True,
    "handledBy": "sandbox",
    "payload": {
        "channel": "telegram",
        "to": "telegram:@benchmark_target",
        "via": "direct",
        "mediaUrl": None,
        "dryRun": True,
    },
}

MEMORY_ERROR = (
    "No matches.\n"
    "[memory] sync failed (session-start): Error: unable to open database file\n"
    "[memory] sync failed (search): Error: unable to open database file"
)
BROWSER_ERROR = (
    "gateway connect failed: Error: gateway closed (1000)\n"
    "Gateway target: ws://127.0.0.1:18789"
)
DIRECTORY_AMBIGUOUS = "Error: multiple channels are configured: feishu, telegram"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def simulate_openclaw_argv(argv: Sequence[str]) -> tuple[int, str]:
    """Return deterministic OpenClaw-compatible output for exact argv."""

    args = [str(item) for item in argv]
    if args and Path(args[0]).name == "openclaw":
        args = args[1:]
    prefix = tuple(args[:2])
    if prefix == ("skills", "list"):
        return 0, _json(SKILLS_INVENTORY)
    if prefix == ("memory", "search"):
        return 1, MEMORY_ERROR
    if prefix == ("browser", "status"):
        return 1, BROWSER_ERROR
    if args[:1] == ["browser"]:
        return 0, _json(
            {
                "status": "sandboxed",
                "title": "Service health and scheduling",
                "availableSlots": [
                    "2026-09-02T10:15",
                    "2026-09-02T11:45",
                    "2026-09-02T13:20",
                ],
                "console": ["warning: deprecated fixture", "error: synthetic timeout"],
                "dom": {"health": "degraded", "retryable": True},
            }
        )
    if prefix == ("cron", "list"):
        return 0, _json({"jobs": []})
    if args[:1] == ["cron"]:
        return 0, _json(
            {
                "id": "cron-sandbox-001",
                "cron": "17 10 2 9 *",
                "recurring": False,
                "status": "created",
            }
        )
    if prefix == ("directory", "self"):
        return 1, DIRECTORY_AMBIGUOUS
    if args[:3] == ["directory", "peers", "list"]:
        channel = "unknown"
        if "--channel" in args:
            index = args.index("--channel")
            if index + 1 < len(args):
                channel = args[index + 1]
        peers = (
            [{"kind": "user", "id": "sandbox-user", "channel": channel}]
            if channel == "feishu"
            else []
        )
        return 0, _json(peers)
    if prefix == ("agents", "list"):
        return 0, _json(AGENTS_INVENTORY)
    if args[:1] == ["sessions"]:
        return 0, _json(SESSIONS_INVENTORY)
    if prefix == ("message", "send"):
        if "--dry-run" not in args:
            return 2, "Error: sandbox message execution requires --dry-run"
        return 0, _json(MESSAGE_DRY_RUN)
    if args[:1] == ["gateway"]:
        return 1, BROWSER_ERROR
    if args[:2] == ["config", "get"]:
        return 0, _json({"channels": ["feishu", "telegram"]})
    return 2, "Error: unsupported sandbox OpenClaw command"


def native_environment_snapshot(surfaces: Sequence[str]) -> dict[str, Any]:
    """Match the pinned grader's native_environment audit-state shape."""

    ready = sorted(
        item["name"] for item in SKILLS_INVENTORY["skills"] if item["eligible"]
    )
    missing = sorted(
        item["name"] for item in SKILLS_INVENTORY["skills"] if not item["eligible"]
    )
    snapshot: dict[str, Any] = {
        "version": 1,
        "surfaces": sorted(set(surfaces)),
    }
    values: dict[str, Any] = {
        "skills": {
            "status": "ready",
            "ready_count": len(ready),
            "missing_count": len(missing),
            "workspace_dir": "/sandbox/workspace",
            "managed_skills_dir": "/sandbox/skills",
            "ready_examples": ready[:3],
            "missing_examples": missing[:3],
            "ready_list": ready,
            "missing_list": missing,
            "first_missing_family_by_skill": {
                "one-password": "bins",
                "slack": "env",
                "zeta": "bins",
            },
        },
        "memory": {"status": "other_failure", "failure_mode": "database_unavailable"},
        "browser": {
            "status": "gateway_closed",
            "gateway_target": "ws://127.0.0.1:18789",
        },
        "cron": {"status": "ready"},
        "directory": {
            "status": "other_failure",
            "ambiguous_self_lookup": True,
            "configured_channels": ["feishu", "telegram"],
            "self_keys": [],
        },
        "agents": {
            "status": "ready",
            "count": len(AGENTS_INVENTORY),
            "default_model": "claude-fable-5",
        },
        "sessions": {
            "status": "ready",
            "count": 2,
            "over_context_limit": ["agent:main:main"],
            "largest_session_key": "agent:main:main",
            "largest_session_input_tokens": 255_678,
        },
        "message": {"status": "dry_run_only"},
    }
    for surface in snapshot["surfaces"]:
        snapshot[surface] = values.get(surface, {"status": "unsupported_surface"})
    return snapshot


def tool_schemas(tool_names: Sequence[str]) -> tuple[dict[str, Any], ...]:
    """Return the exact bounded host schemas needed by one source scenario."""

    definitions = {
        "read": {
            "description": "Read one UTF-8 file inside the isolated scenario workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "maxLength": 512}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        "write": {
            "description": "Write one UTF-8 result file inside the isolated scenario workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "maxLength": 512},
                    "file_path": {"type": "string", "maxLength": 512},
                    "content": {"type": "string", "maxLength": MAX_SANDBOX_FILE_BYTES},
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        "exec": {
            "description": (
                "Run a simulated OpenClaw CLI probe or bounded cat/ls/pwd command; "
                "shell syntax and real subprocesses are unavailable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "maxLength": 4096},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
        "cron": {
            "description": (
                "Create or inspect an in-memory sandbox cron job; no scheduler is touched."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        },
    }
    schemas: list[dict[str, Any]] = []
    for name in tool_names:
        if name not in definitions:
            raise ValueError(f"native scenario requests unsupported tool: {name}")
        definition = definitions[name]
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": definition["description"],
                    "parameters": definition["parameters"],
                },
            }
        )
    return tuple(schemas)


class NativeSandboxHost:
    """RuntimeHostServices implementation confined to one temp workspace."""

    def __init__(self, workspace: Path, protected_paths: Sequence[Path]) -> None:
        self.workspace = workspace.resolve()
        self.protected_paths = frozenset(path.resolve() for path in protected_paths)
        self.trace_events: list[dict[str, Any]] = []
        self.denial_observed = False
        self.recovery_observed = False
        self.successful_calls = 0
        self._sequence = 0
        self._deny_next = True

    def cancellation_requested(self) -> bool:
        return False

    async def emit_background_result(self, result: Any) -> None:
        del result

    def _append(self, event: dict[str, Any]) -> None:
        event["seq"] = self._sequence
        self._sequence += 1
        self.trace_events.append(event)

    def _trace_args(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name == "write":
            path = arguments.get("path") or arguments.get("file_path") or ""
            return {"path": str(path), "file_path": str(path)}
        return {str(key): value for key, value in arguments.items()}

    def _resolve(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw or len(raw) > 512:
            raise ValueError("sandbox path is invalid")
        candidate = (self.workspace / raw).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError("sandbox path escaped the workspace")
        return candidate

    def _read(self, raw: Any) -> str:
        path = self._resolve(raw)
        if not path.is_file() or path.stat().st_size > MAX_SANDBOX_FILE_BYTES:
            raise ValueError("sandbox file is unavailable")
        return path.read_text(encoding="utf-8")

    def _write(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        raw_path = arguments.get("path") or arguments.get("file_path")
        content = arguments.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SANDBOX_FILE_BYTES:
            raise ValueError("sandbox write content is invalid")
        path = self._resolve(raw_path)
        if path in self.protected_paths:
            raise ValueError("sandbox input fixtures are read-only")
        if path.suffix not in {".json", ".md", ".txt", ".yaml", ".yml"}:
            raise ValueError("sandbox output extension is unsupported")
        if path.parent != self.workspace:
            raise ValueError("sandbox writes must stay at workspace root")
        if len(tuple(self.workspace.iterdir())) >= MAX_SANDBOX_FILES and not path.exists():
            raise ValueError("sandbox file limit reached")
        path.write_text(content, encoding="utf-8")
        return {
            "status": "written",
            "path": path.name,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def _exec(self, command: Any) -> Any:
        if not isinstance(command, str) or not command.strip() or len(command) > 4096:
            raise ValueError("sandbox command is invalid")
        if any(token in command for token in (";", "&&", "||", "|", ">", "<", "$(", "`")):
            raise ValueError("sandbox shell syntax is unavailable")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ValueError("sandbox command cannot be parsed") from exc
        if not argv:
            raise ValueError("sandbox command is empty")
        executable = Path(argv[0]).name
        if executable == "openclaw":
            code, output = simulate_openclaw_argv(argv)
            return {"exit_code": code, "stdout": output if code == 0 else "", "stderr": output if code else ""}
        if executable == "cat" and len(argv) == 2:
            return {"exit_code": 0, "stdout": self._read(argv[1]), "stderr": ""}
        if executable == "ls" and len(argv) == 1:
            return {
                "exit_code": 0,
                "stdout": "\n".join(sorted(path.name for path in self.workspace.iterdir())),
                "stderr": "",
            }
        if executable == "pwd" and len(argv) == 1:
            return {"exit_code": 0, "stdout": "/sandbox/workspace", "stderr": ""}
        raise ValueError("sandbox command is outside the allowlist")

    def _cron(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", "create")).lower()
        if action in {"list", "status"}:
            return {"jobs": []}
        return {
            "id": "cron-sandbox-001",
            "cron": str(arguments.get("cron") or arguments.get("schedule") or "17 10 2 9 *"),
            "recurring": bool(arguments.get("recurring", False)),
            "status": "created",
        }

    async def execute_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        traced_args = self._trace_args(name, arguments)
        self._append({"type": "tool_call", "tool": name, "args": traced_args})
        if self._deny_next:
            self._deny_next = False
            self.denial_observed = True
            self._append(
                {
                    "type": "tool_result",
                    "tool": name,
                    "result": "synthetic_transient_denial",
                    "is_error": True,
                }
            )
            return {"error": "synthetic transient denial; retry the same safe operation once"}
        try:
            if name == "read":
                result: Any = self._read(arguments.get("path"))
            elif name == "write":
                result = self._write(arguments)
            elif name == "exec":
                result = self._exec(arguments.get("command"))
            elif name == "cron":
                result = self._cron(arguments)
            else:
                raise ValueError("sandbox tool is unsupported")
        except (OSError, UnicodeError, ValueError):
            self._append(
                {
                    "type": "tool_result",
                    "tool": name,
                    "result": "sandbox_validation_error",
                    "is_error": True,
                }
            )
            return {"error": "sandbox operation rejected"}
        self.successful_calls += 1
        if self.denial_observed:
            self.recovery_observed = True
        self._append(
            {
                "type": "tool_result",
                "tool": name,
                "result": "sanitized_success",
                "is_error": False,
            }
        )
        return result


def write_cli_shim(path: Path) -> None:
    """Create an executable temp shim used only by the pinned source grader."""

    source = f"""#!{sys.executable}
import json
import sys

SKILLS = {SKILLS_INVENTORY!r}
AGENTS = {AGENTS_INVENTORY!r}
SESSIONS = {SESSIONS_INVENTORY!r}
MESSAGE = {MESSAGE_DRY_RUN!r}
MEMORY_ERROR = {MEMORY_ERROR!r}
BROWSER_ERROR = {BROWSER_ERROR!r}
DIRECTORY_AMBIGUOUS = {DIRECTORY_AMBIGUOUS!r}

args = sys.argv[1:]
prefix = tuple(args[:2])
code = 0
if prefix == ("skills", "list"):
    output = json.dumps(SKILLS, sort_keys=True, separators=(",", ":"))
elif prefix == ("memory", "search"):
    code, output = 1, MEMORY_ERROR
elif prefix == ("browser", "status"):
    code, output = 1, BROWSER_ERROR
elif args[:1] == ["browser"]:
    output = json.dumps({{"status": "sandboxed"}}, sort_keys=True)
elif prefix == ("cron", "list"):
    output = json.dumps({{"jobs": []}}, sort_keys=True)
elif args[:1] == ["cron"]:
    output = json.dumps({{"id": "cron-sandbox-001", "status": "created"}}, sort_keys=True)
elif prefix == ("directory", "self"):
    code, output = 1, DIRECTORY_AMBIGUOUS
elif args[:3] == ["directory", "peers", "list"]:
    channel = args[args.index("--channel") + 1] if "--channel" in args else "unknown"
    output = json.dumps([{{"kind": "user", "id": "sandbox-user", "channel": channel}}] if channel == "feishu" else [], sort_keys=True)
elif prefix == ("agents", "list"):
    output = json.dumps(AGENTS, sort_keys=True)
elif args[:1] == ["sessions"]:
    output = json.dumps(SESSIONS, sort_keys=True)
elif prefix == ("message", "send") and "--dry-run" in args:
    output = json.dumps(MESSAGE, sort_keys=True)
elif args[:1] == ["gateway"]:
    code, output = 1, BROWSER_ERROR
elif args[:2] == ["config", "get"]:
    output = json.dumps({{"channels": ["feishu", "telegram"]}}, sort_keys=True)
else:
    code, output = 2, "Error: unsupported sandbox OpenClaw command"

stream = sys.stderr if code else sys.stdout
stream.write(output + "\\n")
raise SystemExit(code)
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def sanitized_environment(*, home: Path, cli_shim: Path) -> dict[str, str]:
    """Minimal environment for the isolated pinned grader subprocess."""

    environment = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "OPENCLAW_BINARY": str(cli_shim),
    }
    for key in ("LANG", "LC_ALL", "TMPDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


__all__ = [
    "NativeSandboxHost",
    "native_environment_snapshot",
    "sanitized_environment",
    "simulate_openclaw_argv",
    "tool_schemas",
    "write_cli_shim",
]

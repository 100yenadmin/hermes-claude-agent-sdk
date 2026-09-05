"""Immutable, secret-free configuration for the SDK session adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .billing import plan_sdk_env_overrides


_MAX_TEXT = 4_096
_MAX_PROMPT_SNAPSHOT_UTF8_BYTES = 1_048_576
_PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "bypassPermissions",
    "dontAsk",
    "auto",
}


def _optional_text(
    value: object, *, field: str, max_length: int = _MAX_TEXT
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if not value or len(value) > max_length or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class SDKSessionConfiguration:
    """Sanitized values that may be retained for one client lifetime.

    ``create`` consumes the explicit parent mapping immediately and retains
    only empty-string credential scrubs plus bounded non-secret overrides.
    Raw environment values are never stored on this object.
    """

    cwd: str
    model: str | None
    permission_mode: str
    prompt_snapshot: str = field(repr=False)
    resume_external_session_id: str | None = field(repr=False)
    env_overrides: tuple[tuple[str, str], ...]
    setting_sources: tuple[str, ...]
    mcp_servers: Mapping[str, object] = field(repr=False, compare=False)
    allowed_tools: tuple[str, ...]
    turn_timeout_seconds: float
    connect_timeout_seconds: float
    close_timeout_seconds: float

    @classmethod
    def create(
        cls,
        *,
        cwd: str,
        model: str | None = None,
        permission_mode: str = "dontAsk",
        prompt_snapshot: str = "",
        resume_external_session_id: str | None = None,
        parent_env: Mapping[str, object] | None = None,
        configured_env: Mapping[str, object] | None = None,
        setting_sources: tuple[str, ...] = (),
        mcp_servers: Mapping[str, object] | None = None,
        allowed_tools: tuple[str, ...] = (),
        turn_timeout_seconds: float = 600.0,
        connect_timeout_seconds: float = 60.0,
        close_timeout_seconds: float = 15.0,
    ) -> "SDKSessionConfiguration":
        safe_cwd = _optional_text(cwd, field="cwd")
        assert safe_cwd is not None
        safe_model = _optional_text(model, field="model")
        if not isinstance(prompt_snapshot, str):
            raise TypeError("prompt_snapshot must be text")
        try:
            prompt_snapshot_bytes = len(prompt_snapshot.encode("utf-8"))
        except UnicodeError:
            raise ValueError("prompt_snapshot is invalid") from None
        if (
            prompt_snapshot_bytes > _MAX_PROMPT_SNAPSHOT_UTF8_BYTES
            or "\x00" in prompt_snapshot
        ):
            raise ValueError("prompt_snapshot is invalid")
        safe_resume = _optional_text(
            resume_external_session_id, field="resume_external_session_id"
        )
        if safe_resume is not None and (
            len(safe_resume) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in safe_resume)
        ):
            raise ValueError("resume_external_session_id is invalid")
        if permission_mode not in _PERMISSION_MODES:
            raise ValueError("permission_mode is invalid")
        if setting_sources != ():
            raise ValueError("setting_sources must be empty")
        if not isinstance(allowed_tools, tuple) or any(
            not isinstance(name, str) or not name or len(name) > _MAX_TEXT
            for name in allowed_tools
        ):
            raise ValueError("allowed_tools is invalid")
        if any(
            not name.startswith("mcp__hermes-tools__")
            or name == "mcp__hermes-tools__"
            for name in allowed_tools
        ):
            raise ValueError("allowed_tools must contain Hermes MCP names")
        if len(set(allowed_tools)) != len(allowed_tools):
            raise ValueError("allowed_tools contains duplicates")
        safe_mcp_servers = dict(mcp_servers or {})
        if any(
            not isinstance(name, str) or not name or len(name) > 256
            for name in safe_mcp_servers
        ):
            raise ValueError("mcp_servers is invalid")
        if any(name != "hermes-tools" for name in safe_mcp_servers):
            raise ValueError("mcp_servers must contain only Hermes MCP")

        timeouts = (
            float(turn_timeout_seconds),
            float(connect_timeout_seconds),
            float(close_timeout_seconds),
        )
        if any(
            not math.isfinite(value) or value <= 0 or value > 86_400
            for value in timeouts
        ):
            raise ValueError("timeouts must be in (0, 86400]")

        planned = plan_sdk_env_overrides(parent_env or {}, configured_env)
        return cls(
            cwd=safe_cwd,
            model=safe_model,
            permission_mode=permission_mode,
            prompt_snapshot=prompt_snapshot,
            resume_external_session_id=safe_resume,
            env_overrides=tuple(sorted(planned.items())),
            setting_sources=setting_sources,
            mcp_servers=MappingProxyType(safe_mcp_servers),
            allowed_tools=allowed_tools,
            turn_timeout_seconds=timeouts[0],
            connect_timeout_seconds=timeouts[1],
            close_timeout_seconds=timeouts[2],
        )

    def option_fields(self) -> dict[str, object]:
        """Return a fresh public ``ClaudeAgentOptions`` field mapping."""

        fields: dict[str, object] = {
            "cwd": self.cwd,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "system_prompt": self.prompt_snapshot,
            "env": dict(self.env_overrides),
            "setting_sources": list(self.setting_sources),
            # Claude-native tools are disabled. Hermes tools are exposed only
            # through the strict, host-owned MCP bridge below.
            "tools": [],
            "mcp_servers": dict(self.mcp_servers),
            "strict_mcp_config": bool(self.mcp_servers),
            "allowed_tools": list(self.allowed_tools),
        }
        if self.resume_external_session_id is not None:
            fields["resume"] = self.resume_external_session_id
        return fields


__all__ = ["SDKSessionConfiguration"]

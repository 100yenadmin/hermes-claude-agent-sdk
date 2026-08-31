"""Immutable, secret-free configuration for the SDK session adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .billing import plan_sdk_env_overrides


_MAX_TEXT = 4_096
_MAX_PROMPT_APPEND = 32_000
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
    system_prompt_append: str | None = field(repr=False)
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
        system_prompt_append: str | None = None,
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
        safe_append = _optional_text(
            system_prompt_append,
            field="system_prompt_append",
            max_length=_MAX_PROMPT_APPEND,
        )
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
        if not isinstance(setting_sources, tuple) or any(
            source not in {"user", "project", "local"} for source in setting_sources
        ):
            raise ValueError("setting_sources is invalid")
        if len(set(setting_sources)) != len(setting_sources):
            raise ValueError("setting_sources contains duplicates")
        if not isinstance(allowed_tools, tuple) or any(
            not isinstance(name, str) or not name or len(name) > _MAX_TEXT
            for name in allowed_tools
        ):
            raise ValueError("allowed_tools is invalid")
        if len(set(allowed_tools)) != len(allowed_tools):
            raise ValueError("allowed_tools contains duplicates")
        safe_mcp_servers = dict(mcp_servers or {})
        if any(
            not isinstance(name, str) or not name or len(name) > 256
            for name in safe_mcp_servers
        ):
            raise ValueError("mcp_servers is invalid")

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
            system_prompt_append=safe_append,
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

        system_prompt: dict[str, str] = {
            "type": "preset",
            "preset": "claude_code",
        }
        if self.system_prompt_append is not None:
            system_prompt["append"] = self.system_prompt_append
        fields: dict[str, object] = {
            "cwd": self.cwd,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "system_prompt": system_prompt,
            "env": dict(self.env_overrides),
            "setting_sources": list(self.setting_sources),
            "tools": [],
            "mcp_servers": dict(self.mcp_servers),
            "strict_mcp_config": bool(self.mcp_servers),
            "allowed_tools": list(self.allowed_tools),
        }
        if self.resume_external_session_id is not None:
            fields["resume"] = self.resume_external_session_id
        return fields


__all__ = ["SDKSessionConfiguration"]

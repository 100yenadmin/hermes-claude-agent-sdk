"""Project Claude Agent SDK-shaped messages onto AgentRuntime v1 events.

This is a dependency-light, duck-typed port of the behavior in
``agent/transports/claude_sdk_event_projector.py`` from the Hermes Claude SDK
parity implementation (source family for PR #65982).  The source projector
builds private OpenAI-shaped Hermes messages; this module preserves its
message-kind and ordering rules while emitting only the public
``agent.runtime_api`` v1 event types.

No SDK package is imported here.  Callers may pass real SDK messages or small
test doubles with the documented attribute shapes.  Unknown message and block
types are ignored, and untrusted values are bounded or replaced with a fixed
sentinel before they reach a public event.

AgentRuntime v1 currently has no dedicated tool-result or reasoning event.
Tool results therefore remain bounded plugin-internal metadata and never
become public content or status events.  Thinking blocks are intentionally not
emitted: private reasoning must not be exposed as user content or status.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent.runtime_api import (
    RuntimeCompletedEvent,
    RuntimeContentEvent,
    RuntimeEvent,
    RuntimeToolRequestEvent,
    RuntimeUsageEvent,
    RuntimeUsageReceipt,
)

__all__ = ["ClaudeSdkEventProjector", "ProjectionResult", "ToolResultMetadata"]


_MAX_TEXT_CHARS = 4_000
_MAX_IDENTIFIER_CHARS = 128
_MAX_ARGUMENT_STRING_CHARS = 2_000
_MAX_ARGUMENT_KEYS = 32
_MAX_ARGUMENT_ITEMS = 32
_MAX_ARGUMENT_DEPTH = 4
_MAX_USAGE_TOKENS = 10**12
_UNAVAILABLE = "[unavailable]"
_UNAVAILABLE_TOOL_INPUT = "[unavailable tool input]"
_UNAVAILABLE_TOOL_RESULT = "[unavailable tool result]"
_SAFE_IDENTIFIER = re.compile(r"[^A-Za-z0-9_.:-]+")


def _sdk_type_name(obj: Any) -> str:
    """Read only the class name; do not invoke arbitrary object methods."""
    return type(obj).__name__


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read a known SDK field without allowing a hostile property to escape."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _safe_text(value: Any, *, limit: int = _MAX_TEXT_CHARS) -> str:
    """Accept text values without stringifying arbitrary SDK objects."""
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _safe_identifier(value: Any, *, default: str) -> str:
    text = _safe_text(value, limit=_MAX_IDENTIFIER_CHARS).strip()
    if not text:
        return default
    text = _SAFE_IDENTIFIER.sub("_", text)
    return text or default


def _safe_json_value(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> Any:
    """Return a bounded JSON-like value without leaking unknown objects.

    SDK tool input is provider-controlled data.  Keeping this conversion
    deliberately narrower than ``str(value)`` avoids invoking hostile
    ``__str__``/``__repr__`` methods and keeps the public event JSON-shaped.
    """
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str):
            return value[:_MAX_ARGUMENT_STRING_CHARS]
        if isinstance(value, int) and not isinstance(value, bool):
            # Public arguments are JSON-compatible; very large integers are
            # bounded to a deterministic sentinel rather than serialized as
            # unbounded data.
            return value if abs(value) <= _MAX_USAGE_TOKENS else _UNAVAILABLE
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _UNAVAILABLE
    if depth >= _MAX_ARGUMENT_DEPTH:
        return _UNAVAILABLE
    identity = id(value)
    if identity in seen:
        return _UNAVAILABLE
    if isinstance(value, Mapping):
        seen.add(identity)
        out: dict[str, Any] = {}
        try:
            items = list(value.items())[:_MAX_ARGUMENT_KEYS]
        except Exception:
            seen.remove(identity)
            return _UNAVAILABLE
        for key, item in items:
            if not isinstance(key, str):
                continue
            out[key[:_MAX_IDENTIFIER_CHARS]] = _safe_json_value(
                item, depth=depth + 1, seen=seen
            )
        seen.remove(identity)
        return out
    if isinstance(value, (list, tuple)):
        seen.add(identity)
        out = [
            _safe_json_value(item, depth=depth + 1, seen=seen)
            for item in value[:_MAX_ARGUMENT_ITEMS]
        ]
        seen.remove(identity)
        return out
    return _UNAVAILABLE


def _safe_tool_arguments(value: Any) -> Mapping[str, Any]:
    """Normalize a ToolUseBlock input to a bounded public mapping."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        normalized = _safe_json_value(value)
        return normalized if isinstance(normalized, dict) else {"input": _UNAVAILABLE_TOOL_INPUT}
    normalized = _safe_json_value(value)
    if normalized == _UNAVAILABLE:
        normalized = _UNAVAILABLE_TOOL_INPUT
    return {"input": normalized}


def _flatten_tool_result_content(content: Any) -> str:
    """Flatten ToolResultBlock content without repr/exception leakage."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:_MAX_TEXT_CHARS]
    if isinstance(content, Mapping):
        safe = _safe_json_value(content)
        try:
            return json.dumps(safe, ensure_ascii=False, sort_keys=True)[:_MAX_TEXT_CHARS]
        except (TypeError, ValueError):
            return _UNAVAILABLE_TOOL_RESULT
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for item in content[:_MAX_ARGUMENT_ITEMS]:
            if isinstance(item, Mapping):
                try:
                    item_type = item.get("type")
                    item_text = item.get("text")
                except Exception:
                    item_type = item_text = None
                if item_type == "text" and isinstance(item_text, str) and item_text:
                    parts.append(item_text[:_MAX_TEXT_CHARS])
                    continue
            safe = _safe_json_value(item)
            try:
                parts.append(json.dumps(safe, ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError):
                parts.append(_UNAVAILABLE_TOOL_RESULT)
        return "\n".join(parts)[:_MAX_TEXT_CHARS]
    return _UNAVAILABLE_TOOL_RESULT


def _coerce_usage_int(value: Any) -> int:
    """Coerce a known SDK token field, rejecting booleans and bad objects."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return min(max(value, 0), _MAX_USAGE_TOKENS)
    if isinstance(value, float):
        if not math.isfinite(value):
            return 0
        return min(max(int(value), 0), _MAX_USAGE_TOKENS)
    if isinstance(value, str):
        text = value.strip()
        if text.isdecimal():
            return min(int(text), _MAX_USAGE_TOKENS)
    return 0


def _known_value(source: Any, key: str) -> Any:
    """Read a known mapping/attribute only; arbitrary values are ignored."""
    if isinstance(source, Mapping):
        try:
            return source.get(key)
        except Exception:
            return None
    return _safe_attr(source, key)


def _safe_cost_status(*, billing_mode: str, total_cost_usd: Any) -> str:
    if billing_mode == "subscription_included":
        return "included"
    if billing_mode == "sdk_reported_metered":
        if isinstance(total_cost_usd, bool):
            return "unknown"
        if isinstance(total_cost_usd, (int, float)):
            try:
                candidate = float(total_cost_usd)
            except (TypeError, ValueError, OverflowError):
                candidate = -1.0
            return "reported" if math.isfinite(candidate) and candidate >= 0 else "unknown"
        if isinstance(total_cost_usd, str):
            try:
                candidate = float(total_cost_usd)
            except (TypeError, ValueError, OverflowError):
                candidate = -1.0
            return "reported" if math.isfinite(candidate) and candidate >= 0 else "unknown"
    return "unknown"


@dataclass(frozen=True)
class ToolResultMetadata:
    """Bounded tool-result data retained for the plugin's turn loop only."""

    tool_use_id: str
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class ProjectionResult:
    """Public events and bounded turn metadata for one SDK message."""

    events: tuple[RuntimeEvent, ...] = field(default_factory=tuple)
    tool_results: tuple[ToolResultMetadata, ...] = field(default_factory=tuple)
    is_tool_iteration: bool = False
    final_text: str | None = None
    is_result: bool = False
    model: str | None = None

    @property
    def tool_result_metadata(self) -> tuple[ToolResultMetadata, ...]:
        """Explicit alias for callers that prefer the longer field name."""
        return self.tool_results


class ClaudeSdkEventProjector:
    """Stateful projector consuming SDK messages in arrival order.

    ``model`` and billing settings are host-provided configuration, not read
    from credentials or environment.  A caller can pass a model-less
    projector and the projector will use a safe model id reported by a message
    when present.
    """

    def __init__(
        self,
        *,
        runtime_id: str = "hermes-claude-agent-sdk",
        provider: str = "claude-agent-sdk",
        model: str | None = None,
        billing_mode: str = "unknown",
        correlation_id: str | None = None,
    ) -> None:
        self._runtime_id = _safe_identifier(
            runtime_id, default="hermes-claude-agent-sdk"
        )
        self._provider = _safe_identifier(provider, default="claude-agent-sdk")
        self._model = _safe_text(model, limit=_MAX_IDENTIFIER_CHARS) or None
        self._billing_mode = (
            billing_mode
            if isinstance(billing_mode, str)
            and billing_mode in {"subscription_included", "sdk_reported_metered"}
            else "unknown"
        )
        self._correlation_id = _safe_identifier(
            correlation_id, default=""
        ) or None

    def project(self, message: Any) -> ProjectionResult:
        name = _sdk_type_name(message)
        if name == "AssistantMessage":
            return self._project_assistant(message)
        if name == "UserMessage":
            return self._project_user(message)
        if name == "ResultMessage":
            return self._project_result(message)
        # SystemMessage, StreamEvent, and unknown lifecycle types are
        # display/bookkeeping only and never enter the public event stream.
        return ProjectionResult()

    def _message_model(self, message: Any) -> str | None:
        reported = _safe_text(
            _safe_attr(message, "model"), limit=_MAX_IDENTIFIER_CHARS
        )
        if reported:
            self._model = reported
        return self._model

    def _project_assistant(self, message: Any) -> ProjectionResult:
        text_parts: list[str] = []
        tool_events: list[RuntimeToolRequestEvent] = []
        for block in _safe_attr(message, "content") or ():
            block_name = _sdk_type_name(block)
            if block_name == "TextBlock":
                text = _safe_text(_safe_attr(block, "text"))
                if text:
                    text_parts.append(text)
            elif block_name == "ToolUseBlock":
                request_id = _safe_identifier(
                    _safe_attr(block, "id"), default="unknown-tool-request"
                )
                name = _safe_identifier(
                    _safe_attr(block, "name"), default="unknown"
                )
                tool_events.append(
                    RuntimeToolRequestEvent(
                        request_id=request_id,
                        name=name,
                        arguments=_safe_tool_arguments(_safe_attr(block, "input")),
                    )
                )
            # ThinkingBlock and ServerToolUseBlock have no safe v1 event type.
            # Their private/provider-specific data is intentionally omitted.

        text = "\n".join(text_parts)[:_MAX_TEXT_CHARS] if text_parts else None
        events: list[RuntimeEvent] = []
        if text:
            events.append(RuntimeContentEvent(text=text))
        events.extend(tool_events)
        return ProjectionResult(
            events=tuple(events),
            final_text=text,
            model=self._message_model(message),
        )

    def _project_user(self, message: Any) -> ProjectionResult:
        """Retain SDK tool results internally without public event emission."""
        content = _safe_attr(message, "content")
        if isinstance(content, str) or not isinstance(content, Sequence):
            return ProjectionResult()

        tool_results: list[ToolResultMetadata] = []
        for block in content:
            if _sdk_type_name(block) != "ToolResultBlock":
                continue
            tool_use_id = _safe_identifier(
                _safe_attr(block, "tool_use_id"), default="unknown-tool-use"
            )
            result_text = _flatten_tool_result_content(
                _safe_attr(block, "content")
            )
            is_error = _safe_attr(block, "is_error", False) is True
            tool_results.append(
                ToolResultMetadata(
                    tool_use_id=tool_use_id,
                    text=result_text,
                    is_error=is_error,
                )
            )
        return ProjectionResult(
            tool_results=tuple(tool_results),
            is_tool_iteration=bool(tool_results),
        )

    def _project_result(self, message: Any) -> ProjectionResult:
        final = _safe_text(_safe_attr(message, "result")) or None
        model = self._message_model(message)
        events: list[RuntimeEvent] = []
        if final:
            # ResultMessage.result is authoritative over preceding text blocks.
            events.append(RuntimeContentEvent(text=final))

        usage = _safe_attr(message, "usage")
        if usage is not None:
            receipt = RuntimeUsageReceipt(
                runtime_id=self._runtime_id,
                provider=self._provider,
                model=model or "unknown",
                billing_mode=self._billing_mode,
                cost_status=_safe_cost_status(
                    billing_mode=self._billing_mode,
                    total_cost_usd=_safe_attr(message, "total_cost_usd"),
                ),
                input_tokens=_coerce_usage_int(
                    _known_value(usage, "input_tokens")
                ),
                output_tokens=_coerce_usage_int(
                    _known_value(usage, "output_tokens")
                ),
                cache_read_tokens=_coerce_usage_int(
                    _known_value(usage, "cache_read_input_tokens")
                    or _known_value(usage, "cache_read_tokens")
                ),
                cache_write_tokens=_coerce_usage_int(
                    _known_value(usage, "cache_creation_input_tokens")
                    or _known_value(usage, "cache_write_tokens")
                ),
                reasoning_tokens=_coerce_usage_int(
                    _known_value(usage, "reasoning_tokens")
                ),
                replay_safe=False,
                correlation_id=self._correlation_id,
            )
            events.append(RuntimeUsageEvent(receipt=receipt))

        completion: dict[str, Any] = {}
        if final:
            completion["text"] = final
        if model:
            completion["model"] = model
        is_error = _safe_attr(message, "is_error")
        if isinstance(is_error, bool) and is_error:
            completion["is_error"] = True
        subtype = _safe_text(_safe_attr(message, "subtype"), limit=64)
        if subtype:
            completion["subtype"] = subtype
        for field_name in ("num_turns", "duration_ms", "duration_api_ms"):
            value = _coerce_usage_int(_safe_attr(message, field_name))
            if value:
                completion[field_name] = value
        events.append(RuntimeCompletedEvent(result=completion or None))
        return ProjectionResult(
            events=tuple(events),
            final_text=final,
            is_result=True,
            model=model,
        )

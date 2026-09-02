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
from itertools import islice
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
_UNKNOWN_MODEL = "unknown"
_SAFE_IDENTIFIER = re.compile(r"[^A-Za-z0-9_.:-]+")
_SAFE_MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")


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


def _safe_model_identifier(value: Any) -> str | None:
    """Read an SDK model identifier without normalizing it into a guess."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _MAX_IDENTIFIER_CHARS or any(
        ord(character) < 32 or ord(character) == 127 for character in text
    ):
        return None
    if (
        text.casefold() == _UNKNOWN_MODEL
        or _SAFE_MODEL_IDENTIFIER.fullmatch(text) is None
    ):
        return None
    return text


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
    selected_model: str | None = None
    effective_model: str | None = None
    canonical_model: str | None = None
    model_resolution: str = "unknown"

    @property
    def tool_result_metadata(self) -> tuple[ToolResultMetadata, ...]:
        """Explicit alias for callers that prefer the longer field name."""
        return self.tool_results


class ClaudeSdkEventProjector:
    """Stateful projector consuming SDK messages in arrival order.

    ``model`` and billing settings are host-provided configuration, not read
    from credentials or environment.  The configured model is retained as
    ``selected_model`` only; effective model identity is read exclusively from
    SDK message/model_usage evidence.
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
        self._selected_model = _safe_model_identifier(model)
        self._reported_models: set[str] = set()
        self._usage_models: set[str] = set()
        self._usage_canonical_models: dict[str, str | None] = {}
        self._usage_malformed = False
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

    def _observe_model_evidence(self, message: Any) -> None:
        reported = _safe_model_identifier(_safe_attr(message, "model"))
        parent_tool_use_id = _safe_attr(message, "parent_tool_use_id")
        if reported and parent_tool_use_id in (None, ""):
            # AssistantMessage.model is primary-route evidence only for the
            # root conversation. Nested agent messages may legitimately use a
            # different model and are already represented in aggregate usage.
            self._reported_models.add(reported)

        model_usage = _safe_attr(message, "model_usage")
        if model_usage is None:
            return
        if not isinstance(model_usage, Mapping):
            self._usage_malformed = True
            return
        try:
            keys = list(islice(model_usage, _MAX_ARGUMENT_ITEMS + 1))
        except Exception:
            self._usage_malformed = True
            return
        if len(keys) > _MAX_ARGUMENT_ITEMS:
            self._usage_malformed = True
            return
        if not keys:
            self._usage_malformed = True
            return
        for model in keys:
            try:
                usage = model_usage[model]
            except Exception:
                self._usage_malformed = True
                continue
            safe_model = _safe_model_identifier(model)
            if safe_model is None or not isinstance(usage, Mapping):
                self._usage_malformed = True
                continue
            canonical = _known_value(usage, "canonicalModel")
            safe_canonical = None
            if canonical is not None and not (
                isinstance(canonical, str)
                and canonical.strip().casefold() == _UNKNOWN_MODEL
            ):
                safe_canonical = _safe_model_identifier(canonical)
                if safe_canonical is None:
                    self._usage_malformed = True
                    continue

            previous_canonical = self._usage_canonical_models.get(safe_model)
            if (
                previous_canonical is not None
                and safe_canonical is not None
                and previous_canonical != safe_canonical
            ):
                self._usage_malformed = True
                continue
            self._usage_models.add(safe_model)
            if safe_canonical is not None:
                self._usage_canonical_models[safe_model] = safe_canonical
            else:
                self._usage_canonical_models.setdefault(safe_model, None)

    def _model_provenance(self) -> tuple[str | None, str | None, str]:
        """Return effective/canonical identity and a fail-closed resolution."""
        if self._usage_malformed:
            return None, None, "ambiguous"

        usage_models = self._usage_models
        reported_models = self._reported_models
        if len(usage_models) > 1:
            # Aggregate model_usage may contain auxiliary models. It cannot
            # identify the primary route by itself, but one independently
            # reported AssistantMessage.model can do so when that identity is
            # also present as a usage key or uniquely names one canonical entry.
            # Missing, conflicting, malformed, or unrelated evidence remains
            # fail-closed.
            if len(reported_models) != 1:
                return None, None, "ambiguous"
            reported = next(iter(reported_models))
            if reported in usage_models:
                effective = reported
                canonical = self._usage_canonical_models.get(reported)
            else:
                canonical_matches = tuple(
                    model
                    for model, model_canonical in self._usage_canonical_models.items()
                    if model_canonical == reported
                )
                if len(canonical_matches) != 1:
                    return None, None, "ambiguous"
                # The root AssistantMessage is the SDK's direct primary-route
                # evidence. A unique usage alias that canonicalizes to it
                # confirms the identity without replacing it with the alias.
                effective = reported
                canonical = reported
        else:
            usage_model = next(iter(usage_models), None)
            canonical = (
                self._usage_canonical_models.get(usage_model)
                if usage_model is not None
                else None
            )
            if usage_model is not None:
                allowed_reported = {usage_model}
                if canonical is not None:
                    allowed_reported.add(canonical)
                if not reported_models.issubset(allowed_reported):
                    return None, None, "ambiguous"
                effective = usage_model
            elif len(reported_models) == 1:
                effective = next(iter(reported_models))
            elif len(reported_models) > 1:
                return None, None, "ambiguous"
            else:
                effective = None

        if effective is None:
            return None, canonical, "unknown"
        if canonical is not None and canonical != effective:
            return effective, canonical, "canonicalized"
        if (
            self._selected_model is not None
            and self._selected_model != effective
        ):
            return effective, canonical, "mismatch"
        return effective, canonical, "exact" if self._selected_model else "reported"

    def _project_assistant(self, message: Any) -> ProjectionResult:
        self._observe_model_evidence(message)
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
        effective, canonical, resolution = self._model_provenance()
        return ProjectionResult(
            events=tuple(events),
            final_text=text,
            model=effective,
            selected_model=self._selected_model,
            effective_model=effective,
            canonical_model=canonical,
            model_resolution=resolution,
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
        self._observe_model_evidence(message)
        is_error = _safe_attr(message, "is_error")
        failed = isinstance(is_error, bool) and is_error
        # The SDK documents ``ResultMessage.result`` as human-readable result
        # prose.  On failed results that field can contain provider error text,
        # so only successful results may become public content or final text.
        final = None if failed else _safe_text(_safe_attr(message, "result")) or None
        effective, canonical, resolution = self._model_provenance()
        model = effective
        receipt_model = canonical or effective or _UNKNOWN_MODEL
        events: list[RuntimeEvent] = []
        if final:
            # ResultMessage.result is authoritative over preceding text blocks.
            events.append(RuntimeContentEvent(text=final))

        usage = _safe_attr(message, "usage")
        if usage is not None:
            receipt = RuntimeUsageReceipt(
                runtime_id=self._runtime_id,
                provider=self._provider,
                model=receipt_model,
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
                selected_model=self._selected_model,
                effective_model=effective,
                canonical_model=canonical,
                model_resolution=resolution,
            )
            events.append(RuntimeUsageEvent(receipt=receipt))

        completion: dict[str, Any] = {}
        if final:
            completion["text"] = final
        # ``model`` is the legacy effective-identity alias.  It must never
        # contain the selected request when the SDK supplied no evidence.
        completion["model"] = receipt_model
        completion["selected_model"] = self._selected_model or _UNKNOWN_MODEL
        completion["effective_model"] = effective or _UNKNOWN_MODEL
        completion["canonical_model"] = canonical or _UNKNOWN_MODEL
        completion["model_resolution"] = resolution
        if failed:
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
            selected_model=self._selected_model,
            effective_model=effective,
            canonical_model=canonical,
            model_resolution=resolution,
        )

"""Small AgentRuntime v1 shell used while the SDK implementation is extracted.

The real Claude Agent SDK session/process implementation belongs to the
provenance-preserving extraction lane.  This module only proves the public
boundary: a zero-argument factory, pure preflight, and conversion of a small
fake SDK event stream into host-owned typed events.  It never calls the SDK's
real query API.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import AsyncIterable, Iterable, Mapping
from typing import Any

from .compatibility import API_MODES, MODEL_PREFIXES, PROVIDER_IDS, RUNTIME_ID


def _read(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(key, default)
    return getattr(event, key, default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ClaudeAgentSDKRuntime:
    """Minimal runtime shell with an intentionally fake-only SDK seam."""

    def __init__(self) -> None:
        # Importing the dependency is delayed until the host has selected and
        # constructed this runtime.  No client, credential, subprocess, or
        # query is created here.
        self._sdk = importlib.import_module("claude_agent_sdk")
        self._closed = False

    def preflight(self, request: Any) -> Any:
        """Return a pure failure for selections outside this descriptor."""

        from agent.runtime_api import RuntimeFailure, RuntimeFailurePhase

        selection = request.selection
        provider_ok = selection.provider in PROVIDER_IDS
        mode_ok = selection.api_mode in API_MODES
        model_ok = any(selection.model.startswith(prefix) for prefix in MODEL_PREFIXES)
        if provider_ok and mode_ok and model_ok:
            return None
        return RuntimeFailure(
            code="claude_runtime_selection_unsupported",
            message="Claude runtime selection is outside its declared descriptor",
            phase=RuntimeFailurePhase.PREFLIGHT,
            replay_safe=True,
        )

    async def _fake_events(self, request: Any) -> AsyncIterable[Any]:
        """Read only the explicit fake event hook; never call ``query``."""

        source_factory = getattr(self._sdk, "iter_events", None)
        if source_factory is None:
            source_factory = getattr(self._sdk, "fake_events", None)
        if not callable(source_factory):
            return

        source = source_factory(request)
        if inspect.isawaitable(source):
            source = await source
        if hasattr(source, "__aiter__"):
            async for event in source:
                yield event
            return
        if isinstance(source, Iterable):
            for event in source:
                yield event

    def _convert_event(self, event: Any, request: Any) -> Any | None:
        """Convert one fake event mapping to a public host event."""

        from agent.runtime_api import (
            RuntimeApprovalRequestEvent,
            RuntimeCancelledEvent,
            RuntimeCompletedEvent,
            RuntimeCompactionEvent,
            RuntimeCompactionPhase,
            RuntimeContentEvent,
            RuntimeEventKind,
            RuntimeFailedEvent,
            RuntimeFailure,
            RuntimeFailurePhase,
            RuntimeStateEnvelope,
            RuntimeStateEvent,
            RuntimeStatusEvent,
            RuntimeToolRequestEvent,
            RuntimeUsageEvent,
            RuntimeUsageReceipt,
        )

        if isinstance(event, (RuntimeContentEvent, RuntimeStatusEvent,
                              RuntimeToolRequestEvent, RuntimeApprovalRequestEvent,
                              RuntimeCompactionEvent, RuntimeStateEvent,
                              RuntimeUsageEvent, RuntimeCompletedEvent,
                              RuntimeCancelledEvent, RuntimeFailedEvent)):
            return event

        kind = str(_read(event, "kind", _read(event, "type", ""))).lower()
        kind = kind.replace("-", "_")
        if kind == RuntimeEventKind.CONTENT.value:
            return RuntimeContentEvent(text=str(_read(event, "text", _read(event, "delta", ""))))
        if kind == RuntimeEventKind.STATUS.value:
            return RuntimeStatusEvent(message=str(_read(event, "message", "")))
        if kind == RuntimeEventKind.TOOL_REQUEST.value:
            arguments = _read(event, "arguments", {})
            return RuntimeToolRequestEvent(
                request_id=str(_read(event, "request_id", "fake-tool-request")),
                name=str(_read(event, "name", "")),
                arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
            )
        if kind == RuntimeEventKind.APPROVAL_REQUEST.value:
            details = _read(event, "details", {})
            return RuntimeApprovalRequestEvent(
                request_id=str(_read(event, "request_id", "fake-approval-request")),
                action=str(_read(event, "action", "")),
                details=dict(details) if isinstance(details, Mapping) else {},
            )
        if kind == RuntimeEventKind.COMPACTION.value:
            phase_value = str(_read(event, "phase", RuntimeCompactionPhase.STARTED.value))
            try:
                phase = RuntimeCompactionPhase(phase_value)
            except ValueError:
                phase = RuntimeCompactionPhase.FAILED
            details = _read(event, "details", {})
            return RuntimeCompactionEvent(
                phase=phase,
                details=dict(details) if isinstance(details, Mapping) else {},
            )
        if kind == RuntimeEventKind.SESSION_STATE.value:
            state = _read(event, "state", {})
            if not isinstance(state, Mapping):
                state = {}
            return RuntimeStateEvent(
                state=RuntimeStateEnvelope(
                    runtime_id=RUNTIME_ID,
                    schema_version=_as_int(_read(event, "schema_version", 1), 1),
                    state=dict(state),
                )
            )
        if kind == RuntimeEventKind.USAGE.value:
            receipt = _read(event, "receipt", event)
            return RuntimeUsageEvent(
                receipt=RuntimeUsageReceipt(
                    runtime_id=RUNTIME_ID,
                    provider=str(_read(receipt, "provider", request.selection.provider)),
                    model=str(_read(receipt, "model", request.selection.model)),
                    billing_mode=str(_read(receipt, "billing_mode", "unknown")),
                    cost_status=str(_read(receipt, "cost_status", "unknown")),
                    input_tokens=_as_int(_read(receipt, "input_tokens", 0)),
                    output_tokens=_as_int(_read(receipt, "output_tokens", 0)),
                    cache_read_tokens=_as_int(_read(receipt, "cache_read_tokens", 0)),
                    cache_write_tokens=_as_int(_read(receipt, "cache_write_tokens", 0)),
                    reasoning_tokens=_as_int(_read(receipt, "reasoning_tokens", 0)),
                    replay_safe=bool(_read(receipt, "replay_safe", False)),
                    correlation_id=_read(receipt, "correlation_id", request.correlation_id),
                )
            )
        if kind == RuntimeEventKind.COMPLETED.value:
            result = _read(event, "result", {})
            return RuntimeCompletedEvent(
                result=dict(result) if isinstance(result, Mapping) else {"text": str(result)}
            )
        if kind == RuntimeEventKind.CANCELLED.value:
            return RuntimeCancelledEvent(reason=str(_read(event, "reason", "cancelled")))
        if kind == RuntimeEventKind.FAILED.value:
            failure = _read(event, "failure", event)
            phase_value = str(_read(failure, "phase", RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT.value))
            try:
                phase = RuntimeFailurePhase(phase_value)
            except ValueError:
                phase = RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT
            return RuntimeFailedEvent(
                failure=RuntimeFailure(
                    code=str(_read(failure, "code", "sdk_runtime_failure")),
                    message=str(_read(failure, "message", "Claude runtime failed")),
                    phase=phase,
                    replay_safe=bool(_read(failure, "replay_safe", False)),
                    retryable=bool(_read(failure, "retryable", False)),
                )
            )
        return None

    async def run_turn(self, request: Any, host: Any):
        """Yield converted fake events and exactly one terminal event."""

        from agent.runtime_api import (
            RuntimeCancelledEvent,
            RuntimeCompletedEvent,
            RuntimeFailedEvent,
            RuntimeFailure,
            RuntimeFailurePhase,
        )

        if host.cancellation_requested():
            yield RuntimeCancelledEvent(reason="cancelled before runtime start")
            return

        content: list[str] = []
        terminal = False
        try:
            async for raw_event in self._fake_events(request):
                event = self._convert_event(raw_event, request)
                if event is None:
                    continue
                if getattr(event, "text", None) is not None:
                    content.append(str(event.text))
                yield event
                if isinstance(event, (RuntimeCompletedEvent, RuntimeCancelledEvent, RuntimeFailedEvent)):
                    terminal = True
                    return
        except Exception:
            # Do not surface raw SDK exception text: real SDK errors can carry
            # request data or credentials.  The extraction lane will add the
            # provider-owned diagnostic mapping behind the same event shape.
            from agent.runtime_api import RuntimeFailurePhase

            yield RuntimeFailedEvent(
                failure=RuntimeFailure(
                    code="sdk_event_source_failed",
                    message="Claude runtime event source failed",
                    phase=(
                        RuntimeFailurePhase.AFTER_VISIBLE_OUTPUT
                        if content
                        else RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT
                    ),
                    replay_safe=not content,
                )
            )
            return

        if not terminal:
            yield RuntimeCompletedEvent(result={"text": "".join(content)})

    async def close(self) -> None:
        self._closed = True


def create_runtime() -> ClaudeAgentSDKRuntime:
    """Zero-argument factory retained only after host compatibility passes."""

    return ClaudeAgentSDKRuntime()


runtime_factory = create_runtime


__all__ = ["ClaudeAgentSDKRuntime", "create_runtime", "runtime_factory"]

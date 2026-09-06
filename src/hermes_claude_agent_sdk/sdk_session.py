"""Thin public-API session adapter for the admitted Claude Agent SDK range.

The adapter owns exactly one lifetime reader for a connected SDK client.  A
turn claims an inbox before ``query`` is issued, and an async lock serializes
claims so two callers can never consume the same stream concurrently.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .billing import (
    BillingBlockReason,
    BillingDecision,
    SDKBillingEvidence,
    classify_sdk_billing,
    extract_sdk_billing_evidence,
)
from .configuration import SDKSessionConfiguration
from .compaction import (
    DEFAULT_COMPACTION_WATCHDOG_SECONDS,
    NativeCompactionLifecycle,
    SessionCompactionEvent,
)
from .content_events import (
    ClaudeSdkEventProjector,
    ProjectionResult,
    zero_native_violation,
)
from .turn_input import SDKTurnInput


class SessionOutcome(str, Enum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BILLING_BLOCKED = "billing_blocked"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SessionStateUpdate:
    external_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class SessionTurnResult:
    outcome: SessionOutcome
    final_text: str | None = None
    state_update: SessionStateUpdate = field(default_factory=SessionStateUpdate)
    billing_decision: BillingDecision | None = None
    error_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class _StreamEnded:
    failed: bool = False


@dataclass(frozen=True, slots=True)
class _CompactionWatchdogExpired:
    pass


@dataclass(frozen=True, slots=True)
class _PostTerminalOutput:
    """Marker for SDK output observed after a turn's terminal result."""

    pass


@dataclass(frozen=True, slots=True)
class _NativeToolViolation:
    """Marker for SDK-native tool output rejected before projection."""

    pass


_CANCELLED = object()
_COMPACTION_WATCHDOG_EXPIRED = _CompactionWatchdogExpired()
_IMMEDIATE_BILLING_BLOCKS = {
    BillingBlockReason.API_KEY_SOURCE,
    BillingBlockReason.EXTRA_USAGE,
    BillingBlockReason.OVERAGE,
    BillingBlockReason.CONFLICTING_EVIDENCE,
}

_TERMINAL_FAILURE_CODES = {
    "api_error": "sdk_terminal_api_error",
    "max_turns": "sdk_terminal_max_turns",
}
_SUBTYPE_FAILURE_CODES = {
    "error_during_execution": "sdk_result_error_during_execution",
    "error_max_turns": "sdk_result_error_max_turns",
}


def _classify_result_failure(message: Any) -> tuple[str, bool]:
    """Return a bounded code from SDK fields documented as safe to log."""

    status = getattr(message, "api_error_status", None)
    if type(status) is int and 400 <= status <= 599:
        if status in {401, 403}:
            return f"sdk_api_auth_{status}", False
        if status == 402:
            return "sdk_api_billing_402", False
        if status == 408:
            return "sdk_api_timeout_408", True
        if status == 429:
            return "sdk_api_rate_limit_429", True
        if status in {503, 529}:
            return f"sdk_api_overloaded_{status}", True
        if status >= 500:
            return f"sdk_api_server_error_{status}", True
        return f"sdk_api_error_{status}", False

    terminal_reason = getattr(message, "terminal_reason", None)
    if type(terminal_reason) is str:
        code = _TERMINAL_FAILURE_CODES.get(terminal_reason)
        if code is not None:
            return code, False

    subtype = getattr(message, "subtype", None)
    if type(subtype) is str:
        code = _SUBTYPE_FAILURE_CODES.get(subtype)
        if code is not None:
            return code, False

    return "sdk_result_failed", False


async def _call(callback: Callable[[Any], Any] | None, value: Any) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


def _merge_evidence(
    current: SDKBillingEvidence | None,
    update: SDKBillingEvidence,
) -> SDKBillingEvidence:
    if current is None:
        return update
    return SDKBillingEvidence(
        api_key_source=(
            update.api_key_source
            if update.api_key_source is not None
            else current.api_key_source
        ),
        is_using_overage=(
            update.is_using_overage
            if update.is_using_overage is not None
            else current.is_using_overage
        ),
        overage_status=(
            update.overage_status
            if update.overage_status is not None
            else current.overage_status
        ),
        rate_limit_type=(
            update.rate_limit_type
            if update.rate_limit_type is not None
            else current.rate_limit_type
        ),
        malformed_fields=tuple(
            sorted(set(current.malformed_fields) | set(update.malformed_fields))
        ),
    )


class SDKSession:
    """One public SDK client, one reader, and one serialized turn claimant."""

    def __init__(
        self,
        configuration: SDKSessionConfiguration,
        *,
        sdk_module: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        projector: ClaudeSdkEventProjector | None = None,
        on_projection: Callable[[ProjectionResult], Any] | None = None,
        on_billing_decision: Callable[[BillingDecision], Any] | None = None,
        compaction_watchdog_seconds: float = DEFAULT_COMPACTION_WATCHDOG_SECONDS,
    ) -> None:
        self._configuration = configuration
        self._sdk = sdk_module
        self._client_factory = client_factory
        self._projector = projector or ClaudeSdkEventProjector(
            model=configuration.model
        )
        self._on_projection = on_projection
        self._on_billing_decision = on_billing_decision
        self._compaction = NativeCompactionLifecycle(
            watchdog_seconds=compaction_watchdog_seconds
        )
        self._client: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._active_inbox: asyncio.Queue[Any] | None = None
        self._turn_terminal_seen = False
        self._post_terminal_output = False
        self._post_terminal_native_violation = False
        self._stream_tool_indexes: dict[int, str] = {}
        self._turn_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._cancel_requested = False

    @property
    def can_restart_after_cancel(self) -> bool:
        """Whether runtime may replace this session after an explicit cancel."""

        return self._closed and self._cancel_requested

    def _sdk_module(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("claude_agent_sdk")
        return self._sdk

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("session is closed")
        if self._client is not None:
            return
        async with self._start_lock:
            if self._client is not None:
                return
            sdk = self._sdk_module()
            fields = self._configuration.option_fields()
            fields["hooks"] = self._compaction.build_hooks(sdk)
            if self._client_factory is not None:
                client = self._client_factory(options=fields)
            else:
                options = sdk.ClaudeAgentOptions(**fields)
                client = sdk.ClaudeSDKClient(options=options)
            self._client = client
            try:
                await asyncio.wait_for(
                    client.connect(), self._configuration.connect_timeout_seconds
                )
            except BaseException:
                await self.close()
                raise
            self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        failed = False
        try:
            async for message in self._client.receive_messages():
                if zero_native_violation(
                    message,
                    allowed_tool_names=self._configuration.allowed_tools,
                    stream_tool_indexes=self._stream_tool_indexes,
                ):
                    inbox = self._active_inbox
                    if inbox is not None:
                        inbox.put_nowait(_NativeToolViolation())
                    else:
                        self._post_terminal_native_violation = True
                    continue
                inbox = self._active_inbox
                if inbox is not None:
                    if self._turn_terminal_seen:
                        # A ResultMessage is the sole supported terminal
                        # boundary. Any later SDK output belongs to neither
                        # this turn nor a plugin-owned background loop.
                        inbox.put_nowait(_PostTerminalOutput())
                    else:
                        inbox.put_nowait(message)
                        if type(message).__name__ == "ResultMessage":
                            self._turn_terminal_seen = True
                    continue
                # Once the turn inbox is retired, any SDK output is an
                # unsolicited post-terminal protocol violation. Keep only a
                # boolean receipt; never project, deliver, or retain content.
                self._post_terminal_output = True
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            inbox = self._active_inbox
            if inbox is not None:
                inbox.put_nowait(_StreamEnded(failed=failed))

    async def run_turn(
        self,
        prompt: str | SDKTurnInput,
        *,
        projector: ClaudeSdkEventProjector | None = None,
        on_projection: Callable[[ProjectionResult], Any] | None = None,
        on_billing_decision: Callable[[BillingDecision], Any] | None = None,
        on_compaction_event: Callable[[SessionCompactionEvent], Any] | None = None,
    ) -> SessionTurnResult:
        if not (
            (isinstance(prompt, str) and prompt.strip())
            or isinstance(prompt, SDKTurnInput)
        ):
            return SessionTurnResult(
                SessionOutcome.FAILED, error_code="invalid_prompt"
            )
        async with self._turn_lock:
            if self._closed:
                return SessionTurnResult(SessionOutcome.CLOSED, error_code="session_closed")
            if self._post_terminal_native_violation:
                self._post_terminal_native_violation = False
                self._post_terminal_output = False
                await self._interrupt_then_close()
                return SessionTurnResult(
                    SessionOutcome.FAILED,
                    error_code="sdk_native_tool_unsupported",
                )
            if self._post_terminal_output:
                # A prior terminal result was followed by unsolicited SDK
                # output while no turn was active. Retire the client and
                # report the protocol violation before a new query.
                self._post_terminal_output = False
                await self._interrupt_then_close()
                return SessionTurnResult(
                    SessionOutcome.FAILED,
                    error_code="sdk_post_terminal_output",
                )
            try:
                await self.start()
            except Exception:
                return SessionTurnResult(
                    SessionOutcome.FAILED, error_code="sdk_start_failed"
                )
            if self._cancel_requested:
                return SessionTurnResult(SessionOutcome.CANCELLED)

            inbox: asyncio.Queue[Any] = asyncio.Queue()
            self._active_inbox = inbox
            self._turn_terminal_seen = False
            self._stream_tool_indexes.clear()
            self._compaction.bind(
                on_event=on_compaction_event,
                on_watchdog=lambda: inbox.put_nowait(_COMPACTION_WATCHDOG_EXPIRED),
            )
            turn_projector = projector or self._projector
            turn_projection_callback = (
                on_projection if on_projection is not None else self._on_projection
            )
            turn_billing_callback = (
                on_billing_decision
                if on_billing_decision is not None
                else self._on_billing_decision
            )
            evidence: SDKBillingEvidence | None = None
            final_text: str | None = None
            state = SessionStateUpdate()
            turn_completed = False
            try:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self._configuration.turn_timeout_seconds
                try:
                    await asyncio.wait_for(
                        self._client.query(prompt),
                        max(0.0, deadline - loop.time()),
                    )
                except asyncio.TimeoutError:
                    await self._interrupt_then_close()
                    return SessionTurnResult(
                        SessionOutcome.TIMED_OUT,
                        error_code="sdk_turn_timeout",
                    )
                while True:
                    try:
                        try:
                            message = inbox.get_nowait()
                        except asyncio.QueueEmpty:
                            if self._compaction.active:
                                # Native compaction suspends the ordinary turn
                                # deadline.  Its own bounded watchdog enqueues a
                                # sentinel, so cancellation and stream failure
                                # remain observable while the deadline is paused.
                                message = await inbox.get()
                            else:
                                message = await asyncio.wait_for(
                                    inbox.get(), max(0.0, deadline - loop.time())
                                )
                    except asyncio.TimeoutError:
                        # A PreCompact hook may race the ordinary deadline.
                        # Once active, only the compaction watchdog may label
                        # the turn as a compaction watchdog failure.
                        if self._compaction.active:
                            continue
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.TIMED_OUT,
                            error_code="sdk_turn_timeout",
                        )
                    if message is _CANCELLED or self._cancel_requested:
                        return SessionTurnResult(SessionOutcome.CANCELLED)
                    if isinstance(message, _NativeToolViolation):
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            error_code="sdk_native_tool_unsupported",
                        )
                    if isinstance(message, _PostTerminalOutput):
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            error_code="sdk_post_terminal_output",
                        )
                    if message is _COMPACTION_WATCHDOG_EXPIRED:
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            error_code="sdk_compaction_watchdog",
                        )
                    if isinstance(message, _StreamEnded):
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            error_code=(
                                "sdk_stream_failed" if message.failed else "sdk_stream_ended"
                            ),
                        )

                    compaction_completed = await self._compaction.handle_message(message)
                    if compaction_completed:
                        # Compaction is a supported budget suspension, not
                        # ordinary stream activity.  Resume with one fresh
                        # bounded turn interval after the native boundary.
                        deadline = (
                            loop.time() + self._configuration.turn_timeout_seconds
                        )

                    billing_update = extract_sdk_billing_evidence(message)
                    if billing_update is not None:
                        evidence = _merge_evidence(evidence, billing_update)
                        decision = classify_sdk_billing(evidence)
                        if decision.block_reason in _IMMEDIATE_BILLING_BLOCKS:
                            await _call(turn_billing_callback, decision)
                            await self._interrupt_then_close()
                            return SessionTurnResult(
                                SessionOutcome.BILLING_BLOCKED,
                                final_text=final_text,
                                state_update=state,
                                billing_decision=decision,
                                error_code="sdk_billing_blocked",
                            )

                    if type(message).__name__ != "ResultMessage":
                        projection = turn_projector.project(message)
                        await _call(turn_projection_callback, projection)
                        if projection.final_text is not None:
                            final_text = projection.final_text
                        continue
                    session_id = getattr(message, "session_id", None)
                    if (
                        isinstance(session_id, str)
                        and 0 < len(session_id) <= 512
                        and not any(
                            ord(character) < 32 or ord(character) == 127
                            for character in session_id
                        )
                    ):
                        state = SessionStateUpdate(external_session_id=session_id)
                    decision = classify_sdk_billing(evidence)
                    await _call(turn_billing_callback, decision)
                    if not decision.allowed:
                        # A billing block retires the client.  The policy
                        # forbids any later SDK call on the same child.
                        await self.close()
                        return SessionTurnResult(
                            SessionOutcome.BILLING_BLOCKED,
                            final_text=final_text,
                            state_update=state,
                            billing_decision=decision,
                            error_code="sdk_billing_blocked",
                        )
                    # Result projection contains RuntimeCompletedEvent, so it
                    # is released only after the billing decision is allowed.
                    projection = turn_projector.project(message)
                    await _call(turn_projection_callback, projection)
                    if projection.final_text is not None:
                        final_text = projection.final_text
                    # Give the sole reader one scheduling point to classify
                    # already-emitted post-terminal output.  A marker already
                    # queued behind this ResultMessage is a protocol failure,
                    # never an idle/background result.
                    await asyncio.sleep(0)
                    trailing_post_terminal = False
                    trailing_native_violation = False
                    while True:
                        try:
                            trailing = inbox.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if isinstance(trailing, _PostTerminalOutput):
                            trailing_post_terminal = True
                            break
                        if isinstance(trailing, _NativeToolViolation):
                            trailing_native_violation = True
                            break
                        if isinstance(trailing, _StreamEnded):
                            continue
                    if trailing_native_violation:
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            error_code="sdk_native_tool_unsupported",
                        )
                    if trailing_post_terminal:
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            error_code="sdk_post_terminal_output",
                        )
                    terminal_reason = getattr(message, "terminal_reason", None)
                    if terminal_reason in {"aborted_streaming", "aborted_tools"}:
                        return SessionTurnResult(
                            SessionOutcome.CANCELLED,
                            final_text=final_text,
                            state_update=state,
                            billing_decision=decision,
                        )
                    if getattr(message, "is_error", False) is True:
                        error_code, retryable = _classify_result_failure(message)
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            billing_decision=decision,
                            error_code=error_code,
                            retryable=retryable,
                        )
                    turn_completed = True
                    return SessionTurnResult(
                        SessionOutcome.COMPLETE,
                        final_text=final_text,
                        state_update=state,
                        billing_decision=decision,
                    )
            except asyncio.CancelledError:
                cleanup = asyncio.create_task(self.cancel())
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                await cleanup
                raise
            except Exception:
                await self._interrupt_then_close()
                return SessionTurnResult(
                    SessionOutcome.FAILED,
                    final_text=final_text,
                    state_update=state,
                    error_code="sdk_turn_failed",
                )
            finally:
                if self._active_inbox is inbox:
                    self._active_inbox = None
                self._turn_terminal_seen = False
                self._stream_tool_indexes.clear()
                await self._compaction.end_turn(completed=turn_completed)

    async def cancel(self) -> SessionOutcome:
        if self._closed:
            return SessionOutcome.CANCELLED
        self._cancel_requested = True
        inbox = self._active_inbox
        if inbox is not None:
            inbox.put_nowait(_CANCELLED)
        await self._interrupt_then_close()
        return SessionOutcome.CANCELLED

    async def _interrupt_then_close(self) -> None:
        client = self._client
        if client is not None:
            try:
                await asyncio.wait_for(client.interrupt(), 5.0)
            except Exception:
                pass
        await self.close()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            await self._compaction.end_turn(completed=False)
            inbox = self._active_inbox
            if inbox is not None:
                inbox.put_nowait(_CANCELLED)
            client = self._client
            self._client = None
            if client is not None:
                try:
                    await asyncio.wait_for(
                        client.disconnect(), self._configuration.close_timeout_seconds
                    )
                except Exception:
                    pass
            reader = self._reader_task
            self._reader_task = None
            if reader is not None:
                reader.cancel()
                await asyncio.wait(
                    {reader}, timeout=self._configuration.close_timeout_seconds
                )


__all__ = [
    "SDKSession",
    "SessionOutcome",
    "SessionStateUpdate",
    "SessionTurnResult",
]

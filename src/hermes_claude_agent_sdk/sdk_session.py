"""Thin public-API session adapter for ``claude-agent-sdk`` 0.2.144.

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
from .content_events import ClaudeSdkEventProjector, ProjectionResult


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


@dataclass(frozen=True, slots=True)
class _StreamEnded:
    failed: bool = False


_CANCELLED = object()
_IMMEDIATE_BILLING_BLOCKS = {
    BillingBlockReason.API_KEY_SOURCE,
    BillingBlockReason.EXTRA_USAGE,
    BillingBlockReason.OVERAGE,
    BillingBlockReason.CONFLICTING_EVIDENCE,
}


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
    ) -> None:
        self._configuration = configuration
        self._sdk = sdk_module
        self._client_factory = client_factory
        self._projector = projector or ClaudeSdkEventProjector(
            model=configuration.model
        )
        self._on_projection = on_projection
        self._on_billing_decision = on_billing_decision
        self._client: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._active_inbox: asyncio.Queue[Any] | None = None
        self._turn_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._cancel_requested = False

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
                inbox = self._active_inbox
                if inbox is not None:
                    inbox.put_nowait(message)
                # Messages outside a claimed turn are intentionally discarded;
                # they can never become the next turn's answer.
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            inbox = self._active_inbox
            if inbox is not None:
                inbox.put_nowait(_StreamEnded(failed=failed))

    async def run_turn(self, prompt: str) -> SessionTurnResult:
        if not isinstance(prompt, str) or not prompt.strip():
            return SessionTurnResult(
                SessionOutcome.FAILED, error_code="invalid_prompt"
            )
        async with self._turn_lock:
            if self._closed:
                return SessionTurnResult(SessionOutcome.CLOSED, error_code="session_closed")
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
            evidence: SDKBillingEvidence | None = None
            final_text: str | None = None
            state = SessionStateUpdate()
            try:
                await self._client.query(prompt)
                while True:
                    try:
                        message = await asyncio.wait_for(
                            inbox.get(), self._configuration.turn_timeout_seconds
                        )
                    except asyncio.TimeoutError:
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.TIMED_OUT, error_code="sdk_turn_timeout"
                        )
                    if message is _CANCELLED or self._cancel_requested:
                        return SessionTurnResult(SessionOutcome.CANCELLED)
                    if isinstance(message, _StreamEnded):
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            error_code=(
                                "sdk_stream_failed" if message.failed else "sdk_stream_ended"
                            ),
                        )

                    billing_update = extract_sdk_billing_evidence(message)
                    if billing_update is not None:
                        evidence = _merge_evidence(evidence, billing_update)
                        decision = classify_sdk_billing(evidence)
                        if decision.block_reason in _IMMEDIATE_BILLING_BLOCKS:
                            await _call(self._on_billing_decision, decision)
                            await self._interrupt_then_close()
                            return SessionTurnResult(
                                SessionOutcome.BILLING_BLOCKED,
                                final_text=final_text,
                                state_update=state,
                                billing_decision=decision,
                                error_code="sdk_billing_blocked",
                            )

                    if type(message).__name__ != "ResultMessage":
                        projection = self._projector.project(message)
                        await _call(self._on_projection, projection)
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
                    await _call(self._on_billing_decision, decision)
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
                    projection = self._projector.project(message)
                    await _call(self._on_projection, projection)
                    if projection.final_text is not None:
                        final_text = projection.final_text
                    terminal_reason = getattr(message, "terminal_reason", None)
                    if terminal_reason in {"aborted_streaming", "aborted_tools"}:
                        return SessionTurnResult(
                            SessionOutcome.CANCELLED,
                            final_text=final_text,
                            state_update=state,
                            billing_decision=decision,
                        )
                    if getattr(message, "is_error", False) is True:
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            final_text=final_text,
                            state_update=state,
                            billing_decision=decision,
                            error_code="sdk_result_failed",
                        )
                    return SessionTurnResult(
                        SessionOutcome.COMPLETE,
                        final_text=final_text,
                        state_update=state,
                        billing_decision=decision,
                    )
            except asyncio.CancelledError:
                await self.cancel()
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
            inbox = self._active_inbox
            if inbox is not None:
                inbox.put_nowait(_CANCELLED)
            reader = self._reader_task
            self._reader_task = None
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            client = self._client
            self._client = None
            if client is not None:
                try:
                    await asyncio.wait_for(
                        client.disconnect(), self._configuration.close_timeout_seconds
                    )
                except Exception:
                    pass


__all__ = [
    "SDKSession",
    "SessionOutcome",
    "SessionStateUpdate",
    "SessionTurnResult",
]

"""Thin public-API session adapter for ``claude-agent-sdk`` 0.2.144.

The adapter owns exactly one lifetime reader for a connected SDK client.  A
turn claims an inbox before ``query`` is issued, and an async lock serializes
claims so two callers can never consume the same stream concurrently.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
from collections import deque
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
from .content_events import ClaudeSdkEventProjector, ProjectionResult
from .turn_input import SDKTurnInput


class SessionOutcome(str, Enum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BILLING_BLOCKED = "billing_blocked"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CLOSED = "closed"


class BackgroundSessionOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BackgroundSessionResult:
    """Bounded provider-local value awaiting host-owned delivery."""

    content: str
    outcome: BackgroundSessionOutcome = BackgroundSessionOutcome.COMPLETED


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


@dataclass(frozen=True, slots=True)
class _PendingBackgroundResult:
    result: BackgroundSessionResult
    requires_foreground_trust: bool


@dataclass(frozen=True, slots=True)
class _CompactionWatchdogExpired:
    pass


_CANCELLED = object()
_COMPACTION_WATCHDOG_EXPIRED = _CompactionWatchdogExpired()
_IMMEDIATE_BILLING_BLOCKS = {
    BillingBlockReason.API_KEY_SOURCE,
    BillingBlockReason.EXTRA_USAGE,
    BillingBlockReason.OVERAGE,
    BillingBlockReason.CONFLICTING_EVIDENCE,
}
_MAX_BACKGROUND_BYTES = 16_384
_BACKGROUND_DEDUPLICATION_WINDOW = 64
_BACKGROUND_CALLBACK_TIMEOUT_SECONDS = 5.0


def _bounded_background_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    encoded = normalized.encode("utf-8")[:_MAX_BACKGROUND_BYTES]
    return encoded.decode("utf-8", errors="ignore").strip()


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
        on_background_result: Callable[[BackgroundSessionResult], Any] | None = None,
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
        self._on_background_result = on_background_result
        self._compaction = NativeCompactionLifecycle(
            watchdog_seconds=compaction_watchdog_seconds
        )
        self._client: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._active_inbox: asyncio.Queue[Any] | None = None
        self._turn_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._cancel_requested = False
        self._background_projector = ClaudeSdkEventProjector(model=configuration.model)
        self._background_text = ""
        self._background_evidence: SDKBillingEvidence | None = None
        self._background_blocked = False
        self._foreground_billing_allowed = False
        self._background_fingerprints: deque[bytes] = deque()
        self._background_fingerprint_set: set[bytes] = set()
        self._background_delivery_lock = asyncio.Lock()
        self._background_delivery_enabled = False
        self._pending_background_results: deque[_PendingBackgroundResult] = deque()

    @property
    def can_restart_after_cancel(self) -> bool:
        """Whether runtime may replace this session after an explicit cancel."""

        return self._closed and self._cancel_requested

    @property
    def can_restart_after_close(self) -> bool:
        """Whether a later explicit turn may construct a fresh SDK child."""

        return self._closed

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
                inbox = self._active_inbox
                if inbox is not None:
                    inbox.put_nowait(message)
                    if type(message).__name__ == "ResultMessage":
                        # The reader owns the turn boundary.  Later messages
                        # are idle completions, never the next turn's answer.
                        if self._active_inbox is inbox:
                            self._active_inbox = None
                    continue
                await self._handle_background_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            inbox = self._active_inbox
            if inbox is not None:
                inbox.put_nowait(_StreamEnded(failed=failed))

    async def _handle_background_message(self, message: Any) -> None:
        """Classify one idle SDK burst without exposing provider identifiers."""

        if self._closed:
            return
        billing_update = extract_sdk_billing_evidence(message)
        if billing_update is not None:
            self._background_evidence = _merge_evidence(
                self._background_evidence, billing_update
            )
            decision = classify_sdk_billing(self._background_evidence)
            if decision.block_reason in _IMMEDIATE_BILLING_BLOCKS:
                self._background_blocked = True

        projection = self._background_projector.project(message)
        if type(message).__name__ != "ResultMessage":
            if projection.final_text:
                combined = "\n".join(
                    part for part in (self._background_text, projection.final_text) if part
                )
                self._background_text = _bounded_background_text(combined)
            return

        text = _bounded_background_text(projection.final_text or self._background_text)
        evidence = self._background_evidence
        blocked = self._background_blocked
        self._background_text = ""
        self._background_evidence = None
        self._background_blocked = False
        if not text or blocked:
            return
        if evidence is not None and not classify_sdk_billing(evidence).allowed:
            return

        outcome = (
            BackgroundSessionOutcome.FAILED
            if getattr(message, "is_error", False) is True
            else BackgroundSessionOutcome.COMPLETED
        )
        fingerprint = hashlib.sha256(
            outcome.value.encode("ascii") + b"\0" + text.encode("utf-8")
        ).digest()
        if fingerprint in self._background_fingerprint_set:
            return
        if len(self._background_fingerprints) >= _BACKGROUND_DEDUPLICATION_WINDOW:
            expired = self._background_fingerprints.popleft()
            self._background_fingerprint_set.discard(expired)
        self._background_fingerprints.append(fingerprint)
        self._background_fingerprint_set.add(fingerprint)
        pending = _PendingBackgroundResult(
            result=BackgroundSessionResult(content=text, outcome=outcome),
            requires_foreground_trust=evidence is None,
        )
        deliver_now: BackgroundSessionResult | None = None
        async with self._background_delivery_lock:
            if self._closed:
                return
            if not self._background_delivery_enabled:
                if len(self._pending_background_results) < _BACKGROUND_DEDUPLICATION_WINDOW:
                    self._pending_background_results.append(pending)
                return
            if pending.requires_foreground_trust and not self._foreground_billing_allowed:
                return
            deliver_now = pending.result
        if deliver_now is not None:
            await self._deliver_background_result(deliver_now)

    async def _deliver_background_result(self, result: BackgroundSessionResult) -> None:
        try:
            await asyncio.wait_for(
                _call(self._on_background_result, result),
                _BACKGROUND_CALLBACK_TIMEOUT_SECONDS,
            )
        except Exception:
            # The host owns rejection, retry, and requeue.  A sealed binding
            # must not retire the SDK reader or trigger plugin-local retry.
            return

    async def release_background_results(self) -> None:
        """Open delivery only after the host has observed the turn terminal."""

        deliverable: list[BackgroundSessionResult] = []
        async with self._background_delivery_lock:
            if self._closed:
                self._pending_background_results.clear()
                return
            self._background_delivery_enabled = True
            while self._pending_background_results:
                pending = self._pending_background_results.popleft()
                if (
                    pending.requires_foreground_trust
                    and not self._foreground_billing_allowed
                ):
                    continue
                deliverable.append(pending.result)
        for result in deliverable:
            await self._deliver_background_result(result)

    async def _pause_background_delivery(self) -> None:
        async with self._background_delivery_lock:
            self._background_delivery_enabled = False

    async def _retire_client_for_recovery(self) -> None:
        """Retire a dead SDK stream without permanently closing the session."""

        async with self._close_lock:
            if self._closed:
                return
            client = self._client
            reader = self._reader_task
            self._client = None
            self._reader_task = None
            self._foreground_billing_allowed = False
            self._background_text = ""
            self._background_evidence = None
            self._background_blocked = False
            async with self._background_delivery_lock:
                self._background_delivery_enabled = False
                self._pending_background_results.clear()
            if client is not None:
                try:
                    await asyncio.wait_for(
                        client.disconnect(), self._configuration.close_timeout_seconds
                    )
                except Exception:
                    pass
            if reader is not None and reader is not asyncio.current_task():
                if not reader.done():
                    reader.cancel()
                await asyncio.wait(
                    {reader}, timeout=self._configuration.close_timeout_seconds
                )

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
            try:
                await self.start()
            except Exception:
                return SessionTurnResult(
                    SessionOutcome.FAILED, error_code="sdk_start_failed"
                )
            if self._cancel_requested:
                return SessionTurnResult(SessionOutcome.CANCELLED)

            await self._pause_background_delivery()
            inbox: asyncio.Queue[Any] = asyncio.Queue()
            self._active_inbox = inbox
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
                    if message is _COMPACTION_WATCHDOG_EXPIRED:
                        await self._interrupt_then_close()
                        return SessionTurnResult(
                            SessionOutcome.FAILED,
                            error_code="sdk_compaction_watchdog",
                        )
                    if isinstance(message, _StreamEnded):
                        await self._retire_client_for_recovery()
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
                    self._foreground_billing_allowed = True
                    # Result projection contains RuntimeCompletedEvent, so it
                    # is released only after the billing decision is allowed.
                    projection = turn_projector.project(message)
                    await _call(turn_projection_callback, projection)
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
            async with self._background_delivery_lock:
                self._background_delivery_enabled = False
                self._pending_background_results.clear()
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
    "BackgroundSessionOutcome",
    "BackgroundSessionResult",
    "SDKSession",
    "SessionOutcome",
    "SessionStateUpdate",
    "SessionTurnResult",
]

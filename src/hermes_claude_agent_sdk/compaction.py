"""Bounded Claude CLI native-compaction lifecycle adapter.

``claude-agent-sdk`` 0.2.144 exposes a public ``PreCompact`` hook but no
typed post-compaction hook.  The pinned Claude CLI currently reports the end
of compaction as a public ``SystemMessage`` whose subtype is
``compact_boundary``.  This module keeps that empirical compatibility detail
inside the plugin and projects only the provider-neutral lifecycle phases to
AgentRuntime v1.

The local watchdog is evidence that a boundary was not observed before the
bound.  It is not a claim about why the provider stopped making progress.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


DEFAULT_COMPACTION_WATCHDOG_SECONDS = 600.0


class SessionCompactionPhase(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    WATCHDOG = "watchdog"


@dataclass(frozen=True, slots=True)
class SessionCompactionEvent:
    """Provider-local lifecycle value safe to translate into host events."""

    phase: SessionCompactionPhase
    trigger: str = "auto"
    watchdog_seconds: float | None = None


def _trigger(value: object) -> str:
    return value if isinstance(value, str) and value in {"auto", "manual"} else "auto"


async def _call(callback: Callable[[Any], Any] | None, value: Any) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


async def _call_without_argument(callback: Callable[[], Any] | None) -> None:
    if callback is None:
        return
    result = callback()
    if inspect.isawaitable(result):
        await result


class NativeCompactionLifecycle:
    """Track one turn's native compaction with an exactly-once terminal phase."""

    def __init__(
        self,
        *,
        watchdog_seconds: float = DEFAULT_COMPACTION_WATCHDOG_SECONDS,
    ) -> None:
        value = float(watchdog_seconds)
        if not math.isfinite(value) or value <= 0 or value > 86_400:
            raise ValueError("compaction watchdog must be in (0, 86400]")
        self._watchdog_seconds = value
        self._on_event: Callable[[SessionCompactionEvent], Any] | None = None
        self._on_watchdog: Callable[[], Any] | None = None
        self._active_depth = 0
        self._active_trigger = "auto"
        self._watchdog_task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self._active_depth > 0

    def bind(
        self,
        *,
        on_event: Callable[[SessionCompactionEvent], Any] | None = None,
        on_watchdog: Callable[[], Any] | None = None,
    ) -> None:
        if self.active:
            raise RuntimeError("cannot replace compaction callbacks while active")
        self._on_event = on_event
        self._on_watchdog = on_watchdog

    def build_hooks(self, sdk_module: Any) -> dict[str, list[Any]]:
        """Build the public SDK PreCompact hook without importing the SDK."""

        hook_matcher = getattr(sdk_module, "HookMatcher", None)
        if not callable(hook_matcher):
            raise RuntimeError(
                "claude-agent-sdk 0.2.144 PreCompact hook support is unavailable"
            )

        async def on_pre_compact(input_data, _tool_use_id, _context):
            try:
                raw_trigger = (
                    input_data.get("trigger")
                    if isinstance(input_data, dict)
                    else None
                )
                await self.start(_trigger(raw_trigger))
            except Exception:
                # A reporting failure must never reject or delay compaction.
                pass
            return {}

        return {"PreCompact": [hook_matcher(hooks=[on_pre_compact])]}

    async def start(self, trigger: str = "auto") -> None:
        normalized = _trigger(trigger)
        if self._active_depth > 0:
            self._active_depth += 1
            return
        self._active_depth = 1
        self._active_trigger = normalized
        self._watchdog_task = asyncio.create_task(self._watchdog())
        await self._emit(
            SessionCompactionEvent(
                phase=SessionCompactionPhase.STARTED,
                trigger=normalized,
            )
        )

    async def handle_message(self, message: Any) -> bool:
        """Consume the pinned CLI's empirical compact-boundary message."""

        if (
            type(message).__name__ != "SystemMessage"
            or getattr(message, "subtype", None) != "compact_boundary"
            or not self.active
        ):
            return False
        data = getattr(message, "data", None)
        metadata = None
        if isinstance(data, dict):
            metadata = data.get("compact_metadata") or data.get("compactMetadata")
        raw_trigger = metadata.get("trigger") if isinstance(metadata, dict) else None
        return await self.complete(_trigger(raw_trigger))

    async def complete(self, trigger: str = "auto") -> bool:
        if not self.active:
            return False
        self._active_depth -= 1
        if self._active_depth > 0:
            return False
        await self._finish(
            SessionCompactionEvent(
                phase=SessionCompactionPhase.COMPLETED,
                trigger=_trigger(trigger),
            )
        )
        return True

    async def expire(self) -> bool:
        """Emit the bounded watchdog once and request turn interruption."""

        if not self.active:
            return False
        await self._finish(
            SessionCompactionEvent(
                phase=SessionCompactionPhase.WATCHDOG,
                trigger=self._active_trigger,
                watchdog_seconds=self._watchdog_seconds,
            )
        )
        await self._notify_watchdog()
        return True

    async def end_turn(self, *, completed: bool) -> None:
        """Close a missing boundary at the turn's proven terminal edge.

        A successful ResultMessage is the historical compatibility fallback
        used by the extracted runtime when the CLI omits ``compact_boundary``.
        Any other terminal outcome marks an active compaction failed.
        """

        if self.active:
            await self._finish(
                SessionCompactionEvent(
                    phase=(
                        SessionCompactionPhase.COMPLETED
                        if completed
                        else SessionCompactionPhase.FAILED
                    ),
                    trigger=self._active_trigger,
                )
            )
        self._on_event = None
        self._on_watchdog = None

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(self._watchdog_seconds)
        except asyncio.CancelledError:
            return
        await self.expire()

    async def _finish(self, event: SessionCompactionEvent) -> None:
        self._active_depth = 0
        task = self._watchdog_task
        self._watchdog_task = None
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._emit(event)

    async def _emit(self, event: SessionCompactionEvent) -> None:
        try:
            await _call(self._on_event, event)
        except Exception:
            # Status/lifecycle reporting cannot break the provider turn.
            return

    async def _notify_watchdog(self) -> None:
        try:
            await _call_without_argument(self._on_watchdog)
        except Exception:
            return


__all__ = [
    "DEFAULT_COMPACTION_WATCHDOG_SECONDS",
    "NativeCompactionLifecycle",
    "SessionCompactionEvent",
    "SessionCompactionPhase",
]

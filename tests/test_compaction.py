from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from hermes_claude_agent_sdk.compaction import (
    NativeCompactionLifecycle,
    SessionCompactionPhase,
)


class _HookMatcher:
    def __init__(self, *, hooks):
        self.hooks = hooks


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, object]


def test_precompact_hook_emits_started_and_never_blocks_on_callback_failure() -> None:
    async def scenario() -> None:
        events = []

        async def record_then_fail(event) -> None:
            events.append(event)
            raise RuntimeError("synthetic status sink failure")

        lifecycle = NativeCompactionLifecycle(watchdog_seconds=1)
        lifecycle.bind(on_event=record_then_fail)
        hooks = lifecycle.build_hooks(SimpleNamespace(HookMatcher=_HookMatcher))
        callback = hooks["PreCompact"][0].hooks[0]

        result = await callback({"trigger": "manual"}, None, {"signal": None})
        await lifecycle.end_turn(completed=False)

        assert result == {}
        assert events[0].phase is SessionCompactionPhase.STARTED
        assert events[0].trigger == "manual"
        assert events[-1].phase is SessionCompactionPhase.FAILED

    asyncio.run(scenario())


def test_compact_boundary_completes_only_an_active_lifecycle() -> None:
    async def scenario() -> None:
        events = []
        lifecycle = NativeCompactionLifecycle(watchdog_seconds=1)
        lifecycle.bind(on_event=events.append)
        hooks = lifecycle.build_hooks(SimpleNamespace(HookMatcher=_HookMatcher))
        callback = hooks["PreCompact"][0].hooks[0]

        assert await lifecycle.handle_message(SystemMessage("init", {})) is False
        assert (
            await lifecycle.handle_message(
                SystemMessage(
                    "compact_boundary",
                    {"compact_metadata": {"trigger": "auto"}},
                )
            )
            is False
        )

        await callback({}, None, None)
        assert (
            await lifecycle.handle_message(
                SystemMessage(
                    "compact_boundary",
                    {"compactMetadata": {"trigger": "manual"}},
                )
            )
            is True
        )
        await lifecycle.end_turn(completed=True)

        assert [event.phase for event in events] == [
            SessionCompactionPhase.STARTED,
            SessionCompactionPhase.COMPLETED,
        ]
        assert events[0].trigger == "auto"
        assert events[1].trigger == "manual"

    asyncio.run(scenario())


def test_untrusted_trigger_values_default_without_stringification() -> None:
    async def scenario() -> None:
        events = []
        lifecycle = NativeCompactionLifecycle(watchdog_seconds=1)
        lifecycle.bind(on_event=events.append)
        callback = lifecycle.build_hooks(
            SimpleNamespace(HookMatcher=_HookMatcher)
        )["PreCompact"][0].hooks[0]

        await callback({"trigger": {"not": "text"}}, None, None)
        await lifecycle.handle_message(
            SystemMessage(
                "compact_boundary",
                {"compact_metadata": {"trigger": ["not", "text"]}},
            )
        )

        assert [event.trigger for event in events] == ["auto", "auto"]

    asyncio.run(scenario())


def test_reentrant_precompact_uses_one_bounded_watchdog() -> None:
    async def scenario() -> None:
        events = []
        aborts = 0

        async def abort_turn() -> None:
            nonlocal aborts
            aborts += 1

        lifecycle = NativeCompactionLifecycle(watchdog_seconds=0.02)
        lifecycle.bind(on_event=events.append, on_watchdog=abort_turn)
        hooks = lifecycle.build_hooks(SimpleNamespace(HookMatcher=_HookMatcher))
        callback = hooks["PreCompact"][0].hooks[0]

        await callback({"trigger": "auto"}, None, None)
        await asyncio.sleep(0.015)
        await callback({"trigger": "manual"}, None, None)
        await asyncio.sleep(0.02)
        await lifecycle.end_turn(completed=False)

        assert [event.phase for event in events] == [
            SessionCompactionPhase.STARTED,
            SessionCompactionPhase.WATCHDOG,
        ]
        assert events[-1].watchdog_seconds == 0.02
        assert aborts == 1

    asyncio.run(scenario())


def test_turn_terminal_fallback_closes_an_active_compaction_exactly_once() -> None:
    async def scenario(*, completed: bool):
        events = []
        lifecycle = NativeCompactionLifecycle(watchdog_seconds=1)
        lifecycle.bind(on_event=events.append)
        callback = lifecycle.build_hooks(
            SimpleNamespace(HookMatcher=_HookMatcher)
        )["PreCompact"][0].hooks[0]

        await callback({"trigger": "auto"}, None, None)
        await lifecycle.end_turn(completed=completed)
        await lifecycle.end_turn(completed=completed)
        return events

    completed_events = asyncio.run(scenario(completed=True))
    failed_events = asyncio.run(scenario(completed=False))

    assert [event.phase for event in completed_events] == [
        SessionCompactionPhase.STARTED,
        SessionCompactionPhase.COMPLETED,
    ]
    assert [event.phase for event in failed_events] == [
        SessionCompactionPhase.STARTED,
        SessionCompactionPhase.FAILED,
    ]

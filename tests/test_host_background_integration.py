"""Cross-repository background delivery contract against the public Hermes host."""

from __future__ import annotations

import asyncio
from queue import Empty, SimpleQueue

import pytest

from agent.runtime_dispatch import HermesRuntimeHostServices
from gateway import session_context
from tools.process_registry import process_registry

from test_runtime_sdk_integration import _request, _runtime


class _SyntheticAgent:
    session_id = "synthetic-parent-session"
    valid_tool_names = frozenset()
    tools = ()
    _interrupt_requested = False


def test_success_with_background_uses_bound_host_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    route = {
        "HERMES_SESSION_KEY": "synthetic:direct:background",
        "HERMES_UI_SESSION_ID": "synthetic-ui",
        "HERMES_SESSION_PLATFORM": "synthetic",
        "HERMES_SESSION_CHAT_TYPE": "direct",
        "HERMES_SESSION_CHAT_ID": "synthetic-chat",
        "HERMES_SESSION_THREAD_ID": "synthetic-thread",
        "HERMES_SESSION_USER_ID": "synthetic-user",
        "HERMES_SESSION_SCOPE_ID": "synthetic-scope",
    }
    monkeypatch.setattr(
        session_context,
        "get_session_env",
        lambda name, default="": route.get(name, default),
    )

    queue: SimpleQueue[dict[str, object]] = SimpleQueue()
    queue_put_after_terminal: list[bool] = []
    terminal_observed = False

    class _RecordingQueue(SimpleQueue[dict[str, object]]):
        def put(self, item: dict[str, object], *args: object, **kwargs: object) -> None:
            queue_put_after_terminal.append(terminal_observed)
            super().put(item, *args, **kwargs)

    queue = _RecordingQueue()
    monkeypatch.setattr(process_registry, "completion_queue", queue)

    async def scenario() -> list[object]:
        nonlocal terminal_observed
        clients: list[object] = []
        runtime = _runtime("success_with_background", clients)
        host = HermesRuntimeHostServices(
            _SyntheticAgent(),
            task_id="synthetic-task",
            runtime_id="claude-agent-sdk",
        )
        events: list[object] = []
        async for event in runtime.run_turn(_request(), host):
            events.append(event)
            if event.kind.value in {"completed", "cancelled", "failed"}:
                terminal_observed = True
        await runtime.close()
        return events

    events = asyncio.run(scenario())

    assert events[-1].kind.value == "completed"
    assert queue_put_after_terminal == [True]
    event = queue.get_nowait()
    with pytest.raises(Empty):
        queue.get_nowait()

    assert event["type"] == "async_delegation"
    assert event["parent_session_id"] == "synthetic-parent-session"
    assert event["status"] == "completed"
    assert event["summary"] == "background queued"
    assert len(event["summary"]) <= 16_384
    assert event["session_key"] == route["HERMES_SESSION_KEY"]
    assert event["origin_ui_session_id"] == route["HERMES_UI_SESSION_ID"]
    assert event["chat_id"] == route["HERMES_SESSION_CHAT_ID"]
    assert "session_id" not in event
    assert "synthetic-hidden-queued" not in repr(event)
    assert "latest" not in repr(event).lower()

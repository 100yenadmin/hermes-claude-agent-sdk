"""Cross-repository background delivery contract against the public Hermes host."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from queue import Empty, SimpleQueue

import pytest


# The standalone plugin's existing host-integration tests use this same
# explicit host-root override. Keeping the host checkout out of the plugin's
# package makes the cross-repository dependency visible at test time without
# binding the suite to one developer-machine path.
_HOST_ROOT_VALUE = os.environ.get("HERMES_AGENT_HOST_ROOT")
if not _HOST_ROOT_VALUE:
    pytest.skip("HERMES_AGENT_HOST_ROOT is not configured", allow_module_level=True)
HOST_ROOT = Path(_HOST_ROOT_VALUE)
if not HOST_ROOT.is_dir():
    pytest.skip("configured Hermes host checkout is absent", allow_module_level=True)
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from agent.runtime_dispatch import HermesRuntimeHostServices
from tools.process_registry import process_registry

from test_runtime_sdk_integration import _request, _runtime


class _SyntheticAgent:
    session_id = "synthetic-parent-session"
    valid_tool_names = frozenset()
    tools = ()
    _interrupt_requested = False


def test_native_background_output_fails_closed_without_host_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: SimpleQueue[dict[str, object]] = SimpleQueue()
    monkeypatch.setattr(process_registry, "completion_queue", queue)

    async def scenario() -> list[object]:
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
        await runtime.close()
        return events

    events = asyncio.run(scenario())

    assert events[-1].kind.value == "failed"
    assert events[-1].failure.code == "sdk_post_terminal_output"
    with pytest.raises(Empty):
        queue.get_nowait()

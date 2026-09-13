"""Ordered producer acknowledgment, independent from display scheduling."""

import asyncio
from types import SimpleNamespace

import pytest

from hermes_claude_agent_sdk.content_events import ProjectionResult
from hermes_claude_agent_sdk.visible_turn import VisibleTurn


class Host:
    def __init__(self):
        self.updates = []
        self.ack = asyncio.Event()
        self.cancelled = False

    async def persist_assistant(self, update):
        await self.ack.wait()
        self.updates.append(update)

    def cancellation_requested(self):
        return self.cancelled


def test_tool_waits_for_producer_and_persistence_ack_not_queue_drain():
    async def run():
        host = Host()
        turn = VisibleTurn(host)
        tool = asyncio.create_task(turn.admit_tool("terminal", {"command": "pwd"}))
        await asyncio.sleep(0)
        assert not tool.done()
        projection = ProjectionResult(final_text="Checking the directory.",
            assistant_message_id="a", tool_bindings=(("native-1", "mcp__hermes-tools__terminal", {"command": "pwd"}),))
        producer = asyncio.create_task(turn.observe(projection))
        await asyncio.sleep(0)
        assert not tool.done() and not host.updates
        host.ack.set()
        await producer
        assert await tool == "native-1"
        assert [u.mode for u in host.updates] == ["snapshot", "final"]
    asyncio.run(run())


def test_long_text_repetition_and_terminal_snapshot_use_identity():
    async def run():
        host = Host()
        host.ack.set()
        turn = VisibleTurn(host)
        text = "code λ\n    keep indentation\n" * 400
        assert await turn.observe(ProjectionResult(final_text=text, assistant_message_id="a")) == text
        assert await turn.observe(ProjectionResult(final_text=text, assistant_message_id="b")) == text
        assert await turn.observe(ProjectionResult(final_text=text, assistant_message_id="b", is_result=True)) == ""
        before = len(host.updates)
        await turn.observe(ProjectionResult(final_text=text, assistant_message_id="b", is_result=True))
        assert len(host.updates) == before
        for identity in ("a", "b"):
            updates = [u for u in host.updates if u.message_id == identity]
            assert "".join(u.text for u in updates) == text
            assert max(map(lambda u: len(u.text), updates)) <= 4000
            assert updates[-1].mode == "final"
    asyncio.run(run())


def test_graceful_interruption_seals_observed_partial_content():
    async def run():
        host = Host()
        host.ack.set()
        turn = VisibleTurn(host)
        await turn.observe(ProjectionResult(final_text="Partial commentary", assistant_message_id="a"))
        await turn.finish()
        assert host.updates[-1].mode == "final"
    asyncio.run(run())


def test_unacknowledged_tool_fails_closed():
    async def run():
        turn = VisibleTurn(Host(), timeout=0.01)
        with pytest.raises(TimeoutError):
            await turn.admit_tool("terminal", {"command": "pwd"})
        assert turn.failure == "tool_transcript_acknowledgment_missing"
    asyncio.run(run())

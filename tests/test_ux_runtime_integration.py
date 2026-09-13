"""Corrected plugin joined to the real host dispatcher and SQLite boundary."""

import asyncio
from types import SimpleNamespace
import pytest

from agent.runtime_dispatch import HermesRuntimeHostServices, _collect_runtime_turn
from agent.turn_context import build_effective_prompt_messages
from hermes_state import SessionDB
from hermes_claude_agent_sdk.compatibility import RUNTIME_ID, build_runtime_descriptor
from hermes_claude_agent_sdk.runtime import ClaudeAgentSDKRuntime
from test_runtime_sdk_integration import (
    _sdk, _Client, _request, SystemMessage, AssistantMessage, TextBlock, ToolUseBlock, ResultMessage,
)


def test_joined_tool_ack_saved_history_usage_and_unchanged_prefix(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="synthetic-parent", source="cli")
    messages = [{"role": "user", "content": "inspect"}]
    clients = []
    sdk = _sdk("success", clients)
    commentary = "Checking safely.\n    Preserve λ and code.\n" * 200
    def flush(rows):
        for row in rows:
            if not row.get("_db_persisted"):
                db.append_message("synthetic-parent", role=row["role"], content=row.get("content"),
                    tool_calls=row.get("tool_calls"), tool_call_id=row.get("tool_call_id"))
                row["_db_persisted"] = True
        return True
    def execute(message, rows, task):
        call = message.tool_calls[0]
        # This reads actual durable state at execution admission, not a queued
        # event or final merge. The tool row follows the complete commentary.
        saved = db.get_messages("synthetic-parent")
        assert saved[-2]["content"] == commentary
        assert saved[-1]["tool_calls"][0]["id"] == call.id
        rows.append({"role": "tool", "tool_call_id": call.id, "content": "harmless result"})
        flush(rows)
    schema = {"type": "function", "function": {"name": "pwd", "description": "Harmless fixture",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
    agent = SimpleNamespace(session_id="synthetic-parent", _session_db=db,
        tools=[schema], valid_tool_names={"pwd"}, _interrupt_requested=False,
        _flush_messages_to_session_db=flush, _execute_tool_calls=execute)
    host = HermesRuntimeHostServices(agent, task_id="synthetic-task", runtime_id=RUNTIME_ID,
        turn_messages=messages, correlation_id="turn-1")

    class Client(_Client):
        async def query(self, prompt):
            self.queries.append(prompt)
            async def produce():
                await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
                if len(self.queries) == 1:
                    await self._messages.put(AssistantMessage([TextBlock(commentary),
                        ToolUseBlock("native-tool-1", "mcp__hermes-tools__pwd", {"path": "."})]))
                    handler = self.options.fields["mcp_servers"]["hermes-tools"]["tools"][0]["handler"]
                    answer = await handler({"path": "."})
                    assert answer["is_error"] is False
                await self._messages.put(AssistantMessage([TextBlock("done")]))
                await self._messages.put(ResultMessage(result="done", usage={"input_tokens": 2, "output_tokens": 3}, num_turns=17))
            self._producer_task = asyncio.create_task(produce())

    def factory(*, options):
        client = Client(options=options, mode="joined")
        clients.append(client)
        return client
    sdk.ClaudeSDKClient = factory
    runtime = ClaudeAgentSDKRuntime(sdk_module=sdk,
        auth_probe=lambda: SimpleNamespace(allowed=True, category="subscription_oauth"), parent_env={})
    async def run():
        request = _request(tools=[schema], messages=messages, correlation_id="turn-1")
        result = await _collect_runtime_turn(runtime, request, host, descriptor=build_runtime_descriptor())
        assert result.completed, result.failure
        assert result.response["api_calls"] is None
        receipts = db.list_runtime_usage_receipts("synthetic-parent")
        assert len(receipts) == 1 and receipts[0].runtime_turn_count == 17
        assert receipts[0].request_count is None
        state = db.get_runtime_state("synthetic-parent", RUNTIME_ID)
        assert state.state["continuity_status"] == "complete"
        messages.append({"role": "user", "content": "continue"})
        host.refresh_turn("synthetic-task", messages, correlation_id="turn-2")
        second = _request(tools=[schema], messages=build_effective_prompt_messages(messages), state=state, correlation_id="turn-2")
        result = await _collect_runtime_turn(runtime, second, host, descriptor=build_runtime_descriptor())
        assert result.completed, result.failure
        assert len(clients) == 1 and len(clients[0].queries) == 2
        assert len(db.list_runtime_usage_receipts("synthetic-parent")) == 2
        assert [m["content"] for m in db.get_messages("synthetic-parent") if not m.get("tool_calls")] == [
            "inspect", commentary, "harmless result", "done", "continue", "done"]
        await runtime.close()
    try:
        asyncio.run(run())
    finally:
        db.close()


@pytest.mark.parametrize("end", ["cancel", "error"])
def test_graceful_partial_stop_preserves_commentary_state_and_one_receipt(tmp_path, end):
    from test_runtime_sdk_integration import _END
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="synthetic-stop", source="cli")
    messages = [{"role": "user", "content": "inspect"}]
    clients = []
    sdk = _sdk("success", clients)
    partial = "Observed partial commentary\n    λ\n" * 180

    def flush(rows):
        for row in rows:
            if not row.get("_db_persisted"):
                db.append_message("synthetic-stop", role=row["role"], content=row.get("content"))
                row["_db_persisted"] = True
        return True

    agent = SimpleNamespace(session_id="synthetic-stop", _session_db=db,
        tools=[], valid_tool_names=set(), _interrupt_requested=False,
        _flush_messages_to_session_db=flush)
    host = HermesRuntimeHostServices(agent, task_id="synthetic-stop-task", runtime_id=RUNTIME_ID,
        turn_messages=messages, correlation_id="synthetic-stop-turn")
    original_ack = host.persist_assistant
    async def acknowledge(update):
        await original_ack(update)
        if end == "cancel":
            agent._interrupt_requested = True
    host.persist_assistant = acknowledge

    class Client(_Client):
        async def query(self, prompt):
            self.queries.append(prompt)
            await self._messages.put(SystemMessage("init", {
                "apiKeySource": "none", "session_id": "synthetic-native-partial"}))
            await self._messages.put(AssistantMessage([TextBlock(partial)]))
            if end == "error":
                await self._messages.put(_END)
    def factory(*, options):
        client = Client(options=options, mode=end)
        clients.append(client)
        return client
    sdk.ClaudeSDKClient = factory
    runtime = ClaudeAgentSDKRuntime(sdk_module=sdk,
        auth_probe=lambda: SimpleNamespace(allowed=True, category="subscription_oauth"), parent_env={})
    async def run():
        result = await asyncio.wait_for(_collect_runtime_turn(runtime,
            _request(messages=messages, correlation_id="synthetic-stop-turn"), host,
            descriptor=build_runtime_descriptor()), timeout=3)
        assert not result.completed
        assert [m["content"] for m in db.get_messages("synthetic-stop")] == ["inspect", partial]
        state = db.get_runtime_state("synthetic-stop", RUNTIME_ID)
        assert state.state["external_session_id"] == "synthetic-native-partial"
        assert state.state["continuity_status"] == "interrupted"
        receipts = db.list_runtime_usage_receipts("synthetic-stop")
        assert len(receipts) == 1
        assert receipts[0].request_count is None and receipts[0].usage_observed is False
        await runtime.close()
        assert clients[0].disconnected == 1
    try:
        asyncio.run(run())
    finally:
        db.close()

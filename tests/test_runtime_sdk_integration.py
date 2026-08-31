"""Offline end-to-end tests for the AgentRuntime/SDK composition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

from agent.runtime_api import RuntimeStateEnvelope
from agent.runtime_dispatch import build_runtime_turn_request

from hermes_claude_agent_sdk.compatibility import RUNTIME_ID
from hermes_claude_agent_sdk.runtime import ClaudeAgentSDKRuntime


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, object]


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class AssistantMessage:
    content: list[object]
    model: str = "claude-fable-synthetic"


@dataclass
class ResultMessage:
    result: str | None = "final"
    usage: dict[str, int] | None = None
    session_id: str = "synthetic-next-session"
    is_error: bool = False
    terminal_reason: str = "completed"
    subtype: str = "success"
    total_cost_usd: float | None = None
    num_turns: int = 1
    duration_ms: int = 1
    duration_api_ms: int = 1


_END = object()


class _Options:
    def __init__(self, **fields: object) -> None:
        self.fields = fields


class _Client:
    def __init__(self, *, options: _Options, mode: str) -> None:
        self.options = options
        self.mode = mode
        self.connected = 0
        self.disconnected = 0
        self.interrupted = 0
        self.queries: list[str] = []
        self._messages: asyncio.Queue[object] = asyncio.Queue()
        self._closed = False

    async def connect(self) -> None:
        self.connected += 1

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if self.mode in {"tool_success", "tool_failure"}:
            server = self.options.fields["mcp_servers"]["hermes-tools"]
            handler = server["tools"][0]["handler"]
            await handler({"path": "."})
            await self._messages.put(
                AssistantMessage([ToolUseBlock("tool-1", "pwd", {"path": "."})])
            )
        if self.mode == "cancel":
            return
        if self.mode not in {"unknown", "tool_failure"}:
            await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
            await self._messages.put(AssistantMessage([TextBlock("hello")]))
        await self._messages.put(
            ResultMessage(
                result="hello",
                usage={"input_tokens": 2, "output_tokens": 3},
            )
        )
        if self.mode == "success_with_background":
            await self._messages.put(AssistantMessage([TextBlock("background queued")]))
            await self._messages.put(
                ResultMessage(
                    result="background queued",
                    session_id="synthetic-hidden-queued",
                )
            )

    async def receive_messages(self):
        while not self._closed:
            message = await self._messages.get()
            if message is _END:
                return
            yield message

    async def interrupt(self) -> None:
        self.interrupted += 1

    async def disconnect(self) -> None:
        self.disconnected += 1
        self._closed = True
        await self._messages.put(_END)


def _sdk(mode: str, clients: list[_Client]) -> ModuleType:
    sdk = ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = _Options

    def make_client(*, options: _Options) -> _Client:
        client = _Client(options=options, mode=mode)
        clients.append(client)
        return client

    def tool(name, description, input_schema):
        def decorate(handler):
            return {
                "name": name,
                "description": description,
                "input_schema": input_schema,
                "handler": handler,
            }

        return decorate

    def create_sdk_mcp_server(*, name, version, tools):
        return {"name": name, "version": version, "tools": tools}

    sdk.ClaudeSDKClient = make_client
    sdk.tool = tool
    sdk.create_sdk_mcp_server = create_sdk_mcp_server
    return sdk


class _Host:
    def __init__(self, *, cancel_after: int | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.cancel_after = cancel_after
        self.cancel_checks = 0
        self.background = []
        self.observed_events: list[str] = []
        self.background_after_terminal: list[bool] = []

    async def execute_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return {"cwd": "/synthetic"}

    async def request_approval(self, action, details):
        return True

    async def emit_status(self, message):
        return None

    async def persist_state(self, state):
        return None

    async def persist_usage(self, receipt):
        return None

    async def emit_compaction(self, event):
        return None

    async def emit_background_result(self, result):
        self.background.append(result)
        self.background_after_terminal.append(
            bool(self.observed_events)
            and self.observed_events[-1] in {"completed", "cancelled", "failed"}
        )

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_after is not None and self.cancel_checks >= self.cancel_after


def _tool_schema():
    return {
        "type": "function",
        "function": {
            "name": "pwd",
            "description": "Return the host working directory",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }


def _request(
    *,
    state=None,
    tools=(),
    correlation_id="synthetic-correlation",
    prompt_snapshot="stable system prompt",
):
    return build_runtime_turn_request(
        provider="claude-agent-sdk",
        model="claude-fable-5",
        api_mode="agent_runtime",
        messages=({"role": "user", "content": "hello runtime"},),
        prompt_snapshot=prompt_snapshot,
        tool_schemas=tools,
        session_state=state,
        correlation_id=correlation_id,
    )


def _runtime(mode: str, clients: list[_Client]) -> ClaudeAgentSDKRuntime:
    return ClaudeAgentSDKRuntime(
        auth_probe=lambda: SimpleNamespace(allowed=True, category="subscription_oauth"),
        sdk_module=_sdk(mode, clients),
        cwd="/synthetic/workspace",
        parent_env={},
    )


async def _collect(runtime, request, host):
    return [event async for event in runtime.run_turn(request, host)]


def test_text_projection_usage_state_terminal_and_public_options() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()
        await runtime.close()

        kinds = [event.kind.value for event in events]
        assert kinds == ["content", "content", "usage", "session_state", "completed"]
        receipt = events[2].receipt
        assert (
            receipt.runtime_id,
            receipt.provider,
            receipt.model,
            receipt.billing_mode,
            receipt.cost_status,
            receipt.correlation_id,
        ) == (
            RUNTIME_ID,
            "claude-agent-sdk",
            "claude-fable-5",
            "subscription_included",
            "included",
            "synthetic-correlation",
        )
        assert dict(events[3].state.state) == {
            "external_session_id": "synthetic-next-session"
        }
        terminal_result = events[4].result
        assert terminal_result["text"] == "hello"
        assert terminal_result["final_response"] == "hello"
        assert terminal_result["completed"] is True
        assert terminal_result["partial"] is False
        assert terminal_result["error"] is None
        assert terminal_result["api_calls"] == 1
        assert terminal_result["provider"] == "claude-agent-sdk"
        assert terminal_result["model"] == "claude-fable-5"
        assert terminal_result["messages"][-1] == {
            "role": "assistant",
            "content": "hello",
        }
        fields = clients[0].options.fields
        assert fields["permission_mode"] == "bypassPermissions"
        assert fields["system_prompt"]["append"].startswith("stable system prompt")
        assert fields["tools"] == []
        assert fields["mcp_servers"]["hermes-tools"]["tools"] == []
        assert clients[0].queries == ["hello runtime"]
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_allowed_bit_with_non_subscription_category_still_rejects_preflight() -> None:
    clients: list[_Client] = []
    runtime = ClaudeAgentSDKRuntime(
        auth_probe=lambda: SimpleNamespace(allowed=True, category="metered"),
        sdk_module=_sdk("success", clients),
        parent_env={},
    )

    failure = runtime.preflight(_request())

    assert failure.code == "claude_subscription_auth_rejected"
    assert failure.replay_safe is False
    assert clients == []


def test_state_v1_rejects_extra_fields_before_auth_or_sdk() -> None:
    clients: list[_Client] = []
    auth_calls = 0

    def auth_probe():
        nonlocal auth_calls
        auth_calls += 1
        return SimpleNamespace(allowed=True, category="subscription_oauth")

    runtime = ClaudeAgentSDKRuntime(
        auth_probe=auth_probe,
        sdk_module=_sdk("success", clients),
        parent_env={},
    )
    state = RuntimeStateEnvelope(
        runtime_id=RUNTIME_ID,
        schema_version=1,
        state={"external_session_id": "synthetic", "extra": True},
    )

    failure = runtime.preflight(_request(state=state))

    assert failure.code == "claude_runtime_state_invalid"
    assert failure.replay_safe is False
    assert auth_calls == 0
    assert clients == []


def test_host_tool_bridge_and_resume_use_only_public_fields() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("tool_success", clients)
        host = _Host()
        state = RuntimeStateEnvelope(
            runtime_id=RUNTIME_ID,
            schema_version=1,
            state={"external_session_id": "synthetic-resume"},
        )
        events = await _collect(runtime, _request(state=state, tools=(_tool_schema(),)), host)
        await runtime.close()

        assert host.calls == [("pwd", {"path": "."})]
        fields = clients[0].options.fields
        assert fields["resume"] == "synthetic-resume"
        assert fields["allowed_tools"] == ["mcp__hermes-tools__pwd"]
        assert fields["strict_mcp_config"] is True
        assert len(fields["mcp_servers"]["hermes-tools"]["tools"]) == 1
        assert [event.kind.value for event in events].count("completed") == 1

    asyncio.run(scenario())


def test_unknown_billing_blocks_success_and_tool_side_effect_is_conservative() -> None:
    async def scenario(mode: str, tools=()):
        clients: list[_Client] = []
        runtime = _runtime(mode, clients)
        host = _Host()
        events = await _collect(runtime, _request(tools=tools), host)
        await runtime.close()
        return events, host

    unknown, _ = asyncio.run(scenario("unknown"))
    after_tool, host = asyncio.run(scenario("tool_failure", (_tool_schema(),)))

    assert [event.kind.value for event in unknown] == ["failed"]
    assert unknown[0].failure.code == "claude_subscription_billing_blocked"
    assert unknown[0].failure.replay_safe is False
    assert [event.kind.value for event in after_tool][-1] == "failed"
    assert after_tool[-1].failure.phase.value == "after_side_effects"
    assert host.calls == [("pwd", {"path": "."})]
    assert not any(event.kind.value in {"usage", "completed"} for event in after_tool)


def test_cancellation_interrupts_and_closes_once_with_one_terminal() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("cancel", clients)
        events = await _collect(runtime, _request(), _Host(cancel_after=3))
        await runtime.close()
        client = clients[0]
        assert [event.kind.value for event in events] == ["cancelled"]
        assert client.interrupted == 1
        assert client.disconnected == 1

    asyncio.run(scenario())


def test_runtime_reuses_one_client_reader_and_uses_host_only_for_idle_completion() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        host = _Host()

        first = await _collect(runtime, _request(), host)
        client = clients[0]
        await client._messages.put(AssistantMessage([TextBlock("background one")]))
        await client._messages.put(
            ResultMessage(result="background one", session_id="synthetic-hidden-background")
        )
        for _ in range(100):
            if host.background:
                break
            await asyncio.sleep(0)
        second = await _collect(runtime, _request(), host)
        await runtime.close()
        await runtime.close()

        assert len(clients) == 1
        assert client.connected == 1
        assert client.disconnected == 1
        assert client.queries == ["hello runtime", "hello runtime"]
        assert sum(event.kind.value == "completed" for event in first) == 1
        assert sum(event.kind.value == "completed" for event in second) == 1
        assert len(host.background) == 1
        assert host.background[0].content == "background one"
        assert set(host.background[0].__dataclass_fields__) == {"content", "outcome"}

    asyncio.run(scenario())


def test_runtime_rejects_prompt_or_tool_contract_change_before_second_query() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        host = _Host()

        await _collect(runtime, _request(), host)
        events = await _collect(
            runtime,
            _request(prompt_snapshot="changed system prompt"),
            host,
        )
        await runtime.close()

        assert [event.kind.value for event in events] == ["failed"]
        assert events[0].failure.code == "claude_runtime_session_contract_changed"
        assert clients[0].queries == ["hello runtime"]

    asyncio.run(scenario())


def test_runtime_rejects_a_replacement_host_binding_without_query_or_reroute() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        first_host = _Host()
        await _collect(runtime, _request(), first_host)

        events = await _collect(runtime, _request(), _Host())
        await runtime.close()

        assert [event.kind.value for event in events] == ["failed"]
        assert events[0].failure.code == "claude_runtime_host_binding_changed"
        assert clients[0].queries == ["hello runtime"]

    asyncio.run(scenario())


def test_queued_idle_burst_is_released_only_after_parent_terminal_is_observed() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success_with_background", clients)
        host = _Host()

        async for event in runtime.run_turn(_request(), host):
            host.observed_events.append(event.kind.value)
        await runtime.close()

        assert host.observed_events[-1] == "completed"
        assert [item.content for item in host.background] == ["background queued"]
        assert host.background_after_terminal == [True]

    asyncio.run(scenario())

"""Offline end-to-end tests for the AgentRuntime/SDK composition."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

from agent.runtime_api import (
    RuntimeCompactionPhase,
    RuntimeStateEnvelope,
    RuntimeToolRequestEvent,
)
from agent.runtime_dispatch import _collect_runtime_turn, build_runtime_turn_request

from hermes_claude_agent_sdk.compatibility import RUNTIME_ID, build_runtime_descriptor
from hermes_claude_agent_sdk.runtime import (
    ClaudeAgentSDKRuntime,
    _safe_tool_observation_name,
)


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
    model: str | None = "claude-fable-synthetic"


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
    model: str | None = None
    model_usage: object | None = None
    api_error_status: int | None = None


_END = object()


class _Options:
    def __init__(self, **fields: object) -> None:
        self.fields = fields


class _HookMatcher:
    def __init__(self, *, hooks) -> None:
        self.hooks = hooks


class _Client:
    def __init__(self, *, options: _Options, mode: str) -> None:
        self.options = options
        self.mode = mode
        self.connected = 0
        self.disconnected = 0
        self.interrupted = 0
        self.queries: list[object] = []
        self._messages: asyncio.Queue[object] = asyncio.Queue()
        self._closed = False
        self._producer_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self.connected += 1

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if self.mode in {
            "compaction",
            "compaction_failure",
            "compaction_watchdog",
            "compaction_tool_success",
        }:
            callback = self.options.fields["hooks"]["PreCompact"][0].hooks[0]
            await callback({"trigger": "auto"}, None, None)
        if self.mode in {"tool_success", "tool_failure", "compaction_tool_success"}:
            server = self.options.fields["mcp_servers"]["hermes-tools"]
            handler = server["tools"][0]["handler"]
            await handler({"path": "."})
            await self._messages.put(
                AssistantMessage([ToolUseBlock("tool-1", "pwd", {"path": "."})])
            )
        if self.mode == "native_agent_once" and len(self.queries) == 1:
            await self._messages.put(
                AssistantMessage([ToolUseBlock("agent-1", "Agent", {})])
            )
        if self.mode in {"cancel", "compaction_watchdog"}:
            return
        if self.mode == "sustained_stream":
            self._producer_task = asyncio.create_task(self._emit_sustained_stream())
            return
        if self.mode == "cancellation_probe_failure":
            await self._messages.put(AssistantMessage([TextBlock("queued before probe failure")]))
            return
        if self.mode not in {"unknown", "tool_failure"}:
            await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
            assistant_model = (
                None
                if self.mode in {"missing_model", "model_ambiguous_same_canonical"}
                else "claude-fable-5"
                if self.mode == "model_canonical"
                else "claude-fable-synthetic"
            )
            await self._messages.put(
                AssistantMessage([TextBlock("hello")], model=assistant_model)
            )
        if self.mode in {"compaction", "compaction_tool_success"}:
            await self._messages.put(
                SystemMessage(
                    "compact_boundary",
                    {"compact_metadata": {"trigger": "auto"}},
                )
            )
        await self._messages.put(
            ResultMessage(
                result=(
                    "private provider failure prose"
                    if self.mode == "api_error_429"
                    else "hello"
                ),
                usage={"input_tokens": 2, "output_tokens": 3},
                is_error=self.mode in {"compaction_failure", "api_error_429"},
                api_error_status=429 if self.mode == "api_error_429" else None,
                model="claude-fable-5" if self.mode == "model_canonical" else None,
                model_usage=(
                    {
                        "claude-fable-5": {
                            "canonicalModel": "claude-fable-5-1",
                            "inputTokens": 2,
                            "outputTokens": 3,
                        }
                    }
                    if self.mode == "model_canonical"
                    else {
                        "claude-fable-5": {
                            "canonicalModel": "claude-fable-5-1",
                            "inputTokens": 2,
                            "outputTokens": 3,
                        },
                        "claude-fable-5-1": {
                            "canonicalModel": "claude-fable-5-1",
                            "inputTokens": 2,
                            "outputTokens": 3,
                        },
                    }
                    if self.mode == "model_ambiguous_same_canonical"
                    else None
                ),
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

    async def _emit_sustained_stream(self) -> None:
        try:
            while not self._closed:
                await self._messages.put(
                    AssistantMessage([TextBlock("sustained projection")])
                )
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            raise

    async def receive_messages(self):
        while not self._closed:
            message = await self._messages.get()
            if message is _END:
                return
            yield message

    async def interrupt(self) -> None:
        self.interrupted += 1
        self._closed = True
        if self._producer_task is not None:
            self._producer_task.cancel()
            await asyncio.gather(self._producer_task, return_exceptions=True)
        await self._messages.put(_END)

    async def disconnect(self) -> None:
        self.disconnected += 1
        self._closed = True
        if self._producer_task is not None:
            self._producer_task.cancel()
            await asyncio.gather(self._producer_task, return_exceptions=True)
        await self._messages.put(_END)


class _InterruptThenSuccessClient(_Client):
    def __init__(self, *, options: _Options, first: bool) -> None:
        super().__init__(options=options, mode="interrupt_then_success")
        self.first = first

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if not self.first:
            await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
            await self._messages.put(
                AssistantMessage([TextBlock("fresh second turn")])
            )
            await self._messages.put(
                ResultMessage(
                    result="fresh second turn",
                    session_id="synthetic-second-session",
                    usage={"input_tokens": 2, "output_tokens": 3},
                )
            )
            return
        await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
        await self._messages.put(
            AssistantMessage([TextBlock("partial interrupted turn")])
        )

    async def interrupt(self) -> None:
        self.interrupted += 1
        # The SDK may have already queued an aborted result when interrupt
        # returns.  Closing the first client prevents that stale tail from
        # crossing into the replacement client's reader.
        self._closed = True
        await self._messages.put(
            AssistantMessage([TextBlock("stale interrupted tail")])
        )
        await self._messages.put(
            ResultMessage(
                result="stale interrupted tail",
                session_id="synthetic-stale-session",
                terminal_reason="aborted_streaming",
            )
        )
        await self._messages.put(_END)


class _SuccessThenInterruptThenSuccessClient(_Client):
    def __init__(self, *, options: _Options, phase: int) -> None:
        super().__init__(options=options, mode="success_interrupt_success")
        self.phase = phase

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if self.phase == 0 and len(self.queries) == 2:
            await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
            await self._messages.put(
                AssistantMessage([TextBlock("partial cancelled turn")])
            )
            return

        text = "turn A" if self.phase == 0 else "turn C"
        session_id = (
            "synthetic-turn-a" if self.phase == 0 else "synthetic-turn-c"
        )
        await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
        await self._messages.put(AssistantMessage([TextBlock(text)]))
        await self._messages.put(
            ResultMessage(
                result=text,
                session_id=session_id,
                usage={"input_tokens": 2, "output_tokens": 3},
            )
        )

    async def interrupt(self) -> None:
        self.interrupted += 1
        self._closed = True
        await self._messages.put(
            AssistantMessage([TextBlock("stale cancelled tail")])
        )
        await self._messages.put(
            ResultMessage(
                result="stale cancelled tail",
                session_id="synthetic-stale-session",
                terminal_reason="aborted_streaming",
            )
        )
        await self._messages.put(_END)


def _sdk(mode: str, clients: list[_Client]) -> ModuleType:
    sdk = ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = _Options
    sdk.HookMatcher = _HookMatcher

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


def _interrupt_then_success_sdk(clients: list[_InterruptThenSuccessClient]) -> ModuleType:
    sdk = ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = _Options
    sdk.HookMatcher = _HookMatcher

    def make_client(*, options: _Options) -> _InterruptThenSuccessClient:
        client = _InterruptThenSuccessClient(options=options, first=not clients)
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


def _success_then_interrupt_then_success_sdk(
    clients: list[_SuccessThenInterruptThenSuccessClient],
) -> ModuleType:
    sdk = ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = _Options
    sdk.HookMatcher = _HookMatcher

    def make_client(*, options: _Options) -> _SuccessThenInterruptThenSuccessClient:
        client = _SuccessThenInterruptThenSuccessClient(
            options=options, phase=len(clients)
        )
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
        self.compaction = []

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
        self.compaction.append(event)

    async def emit_background_result(self, result):
        self.background.append(result)
        self.background_after_terminal.append(
            bool(self.observed_events)
            and self.observed_events[-1] in {"completed", "cancelled", "failed"}
        )

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_after is not None and self.cancel_checks >= self.cancel_after


class _CancelOnceAfterContentHost(_Host):
    def __init__(self) -> None:
        super().__init__()
        self.content_observed = False
        self._cancel_sent = False

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        if self.content_observed and not self._cancel_sent:
            self._cancel_sent = True
            return True
        return False


class _PreSetThenContinueHost(_Host):
    def __init__(self) -> None:
        super().__init__()
        self._pre_set = True

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        if self._pre_set:
            self._pre_set = False
            return True
        return False


class _ArmableContentCancellationHost(_Host):
    def __init__(self) -> None:
        super().__init__()
        self._armed = False
        self.content_observed = False
        self._cancel_sent = False

    def arm(self) -> None:
        self._armed = True
        self.content_observed = False
        self._cancel_sent = False

    def disarm(self) -> None:
        self._armed = False
        self.content_observed = False

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        if self._armed and self.content_observed and not self._cancel_sent:
            self._cancel_sent = True
            return True
        return False


class _ProjectionCountCancellationHost(_Host):
    def __init__(self, *, cancel_after_projections: int) -> None:
        super().__init__()
        self._cancel_after_projections = cancel_after_projections
        self.cancelled_projection_count: int | None = None
        self.projection_count = 0

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        cancelled = self.projection_count >= self._cancel_after_projections
        if cancelled and self.cancelled_projection_count is None:
            self.cancelled_projection_count = self.projection_count
        return cancelled


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
    messages=({"role": "user", "content": "hello runtime"},),
):
    return build_runtime_turn_request(
        provider="claude-agent-sdk",
        model="claude-fable-5",
        api_mode="agent_runtime",
        messages=messages,
        prompt_snapshot=prompt_snapshot,
        tool_schemas=tools,
        session_state=state,
        correlation_id=correlation_id,
    )


def _runtime(
    mode: str,
    clients: list[_Client],
    *,
    compaction_watchdog_seconds: float = 600.0,
) -> ClaudeAgentSDKRuntime:
    return ClaudeAgentSDKRuntime(
        auth_probe=lambda: SimpleNamespace(allowed=True, category="subscription_oauth"),
        sdk_module=_sdk(mode, clients),
        cwd="/synthetic/workspace",
        parent_env={},
        compaction_watchdog_seconds=compaction_watchdog_seconds,
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
            "claude-fable-synthetic",
            "subscription_included",
            "included",
            "synthetic-correlation",
        )
        assert receipt.selected_model == "claude-fable-5"
        assert receipt.effective_model == "claude-fable-synthetic"
        assert receipt.canonical_model is None
        assert receipt.model_resolution == "mismatch"
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
        assert terminal_result["model"] == "claude-fable-synthetic"
        assert terminal_result["selected_model"] == "claude-fable-5"
        assert terminal_result["effective_model"] == "claude-fable-synthetic"
        assert terminal_result["canonical_model"] == "unknown"
        assert terminal_result["model_resolution"] == "mismatch"
        assert terminal_result["messages"][-1] == {
            "role": "assistant",
            "content": "hello",
        }
        fields = clients[0].options.fields
        assert fields["permission_mode"] == "bypassPermissions"
        assert fields["system_prompt"]["append"].startswith("stable system prompt")
        assert fields["tools"] == ["Agent"]
        assert fields["mcp_servers"]["hermes-tools"]["tools"] == []
        assert clients[0].queries == ["hello runtime"]
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_runtime_preserves_safe_sdk_failure_code_and_retryability() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("api_error_429", clients)
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()

        failure = events[-1].failure
        assert events[-1].kind.value == "failed"
        assert failure.code == "sdk_api_rate_limit_429"
        assert failure.retryable is True
        assert failure.replay_safe is False
        assert "private" not in failure.message
        assert all(
            "private provider failure prose" not in getattr(event, "text", "")
            for event in events
        )

    asyncio.run(scenario())


def test_runtime_preserves_canonical_sdk_model_in_usage_and_terminal_result() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("model_canonical", clients)
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()

        receipt = next(event.receipt for event in events if event.kind.value == "usage")
        assert receipt.model == "claude-fable-5-1"
        assert receipt.selected_model == "claude-fable-5"
        assert receipt.effective_model == "claude-fable-5"
        assert receipt.canonical_model == "claude-fable-5-1"
        assert receipt.model_resolution == "canonicalized"
        terminal_result = next(
            event.result for event in events if event.kind.value == "completed"
        )
        assert terminal_result["model"] == "claude-fable-5-1"
        assert terminal_result["selected_model"] == "claude-fable-5"
        assert terminal_result["effective_model"] == "claude-fable-5"
        assert terminal_result["canonical_model"] == "claude-fable-5-1"
        assert terminal_result["model_resolution"] == "canonicalized"

    asyncio.run(scenario())


def test_runtime_does_not_use_selected_model_when_sdk_reports_no_model() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("missing_model", clients)
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()

        receipt = next(event.receipt for event in events if event.kind.value == "usage")
        assert receipt.model == "unknown"
        assert receipt.selected_model == "claude-fable-5"
        assert receipt.effective_model is None
        assert receipt.canonical_model is None
        assert receipt.model_resolution == "unknown"
        terminal_result = next(
            event.result for event in events if event.kind.value == "completed"
        )
        assert terminal_result["model"] == "unknown"
        assert terminal_result["selected_model"] == "claude-fable-5"
        assert terminal_result["effective_model"] == "unknown"
        assert terminal_result["canonical_model"] == "unknown"
        assert terminal_result["model_resolution"] == "unknown"

    asyncio.run(scenario())


def test_runtime_erases_model_identity_for_ambiguous_shared_canonical() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("model_ambiguous_same_canonical", clients)
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()

        receipt = next(event.receipt for event in events if event.kind.value == "usage")
        assert receipt.model == "unknown"
        assert receipt.selected_model == "claude-fable-5"
        assert receipt.effective_model is None
        assert receipt.canonical_model is None
        assert receipt.model_resolution == "ambiguous"
        terminal_result = next(
            event.result for event in events if event.kind.value == "completed"
        )
        assert terminal_result["model"] == "unknown"
        assert terminal_result["selected_model"] == "claude-fable-5"
        assert terminal_result["effective_model"] == "unknown"
        assert terminal_result["canonical_model"] == "unknown"
        assert terminal_result["model_resolution"] == "ambiguous"

    asyncio.run(scenario())


def test_native_image_turn_uses_the_public_sdk_streaming_input() -> None:
    async def scenario():
        payload = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode("ascii")
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        request = _request(
            messages=(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "inspect the fixture"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{payload}"
                            },
                        },
                    ],
                },
            )
        )

        events = await _collect(runtime, request, _Host())
        await runtime.close()

        assert events[-1].kind.value == "completed"
        prompt = clients[0].queries[0]
        messages = [message async for message in prompt]
        assert messages == [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "inspect the fixture"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": payload,
                            },
                        },
                    ],
                },
                "parent_tool_use_id": None,
            }
        ]

    asyncio.run(scenario())


def test_invalid_image_fails_before_sdk_start() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        request = _request(
            messages=(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "file:///tmp/private.png"},
                        }
                    ],
                },
            )
        )

        events = await _collect(runtime, request, _Host())
        await runtime.close()

        assert [event.kind.value for event in events] == ["failed"]
        assert events[0].failure.code == "claude_runtime_image_invalid"
        assert clients == []

    asyncio.run(scenario())


def test_native_compaction_is_a_typed_runtime_event_without_role_injection() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("compaction", clients)
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()

        compaction = [event for event in events if event.kind.value == "compaction"]
        assert [event.phase for event in compaction] == [
            RuntimeCompactionPhase.STARTED,
            RuntimeCompactionPhase.COMPLETED,
        ]
        assert sum(event.kind.value == "completed" for event in events) == 1
        terminal = events[-1]
        assert [message["role"] for message in terminal.result["messages"]] == [
            "user",
            "assistant",
        ]

    asyncio.run(scenario())


def test_native_compaction_is_projected_through_the_host_dispatcher() -> None:
    async def scenario() -> None:
        clients: list[_Client] = []
        runtime = _runtime("compaction", clients)
        host = _Host()

        result = await _collect_runtime_turn(
            runtime,
            _request(),
            host,
            descriptor=build_runtime_descriptor(),
        )
        await runtime.close()

        assert [event.phase for event in host.compaction] == [
            RuntimeCompactionPhase.STARTED,
            RuntimeCompactionPhase.COMPLETED,
        ]
        assert result.terminal.kind.value == "completed"

    asyncio.run(scenario())


def test_native_compaction_failure_and_watchdog_are_typed_before_turn_failure() -> None:
    async def scenario(mode: str, *, watchdog_seconds: float = 600.0):
        clients: list[_Client] = []
        runtime = _runtime(
            mode,
            clients,
            compaction_watchdog_seconds=watchdog_seconds,
        )
        events = await _collect(runtime, _request(), _Host())
        await runtime.close()
        return events

    failed = asyncio.run(scenario("compaction_failure"))
    watchdog = asyncio.run(
        scenario("compaction_watchdog", watchdog_seconds=0.01)
    )

    assert [
        event.phase
        for event in failed
        if event.kind.value == "compaction"
    ] == [RuntimeCompactionPhase.STARTED, RuntimeCompactionPhase.FAILED]
    assert failed[-1].kind.value == "failed"
    assert [
        event.phase
        for event in watchdog
        if event.kind.value == "compaction"
    ] == [RuntimeCompactionPhase.STARTED, RuntimeCompactionPhase.WATCHDOG]
    assert watchdog[-1].kind.value == "failed"
    assert watchdog[-1].failure.code == "sdk_compaction_watchdog"


def test_active_compaction_cancellation_projects_failed_before_turn_terminal() -> None:
    async def scenario() -> None:
        clients: list[_Client] = []
        runtime = _runtime("compaction_watchdog", clients)
        host = _Host(cancel_after=2)

        result = await _collect_runtime_turn(
            runtime,
            _request(),
            host,
            descriptor=build_runtime_descriptor(),
        )
        await runtime.close()

        assert [event.phase for event in host.compaction] == [
            RuntimeCompactionPhase.STARTED,
            RuntimeCompactionPhase.FAILED,
        ]
        assert result.terminal.kind.value == "cancelled"
        assert sum(
            event.kind.value in {"completed", "cancelled", "failed"}
            for event in result.events
        ) == 1
        assert clients[0].interrupted == 1
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


def test_native_agent_observation_is_non_executable_and_resets_between_turns() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("native_agent_once", clients)
        host = _Host()

        first = await _collect(runtime, _request(), host)
        first_observations = runtime.last_turn_tool_observations
        second = await _collect(runtime, _request(), host)
        second_observations = runtime.last_turn_tool_observations
        await runtime.close()
        return first, second, first_observations, second_observations, host

    first, second, first_observations, second_observations, host = asyncio.run(
        scenario()
    )

    assert first_observations == ("Agent",)
    assert second_observations == ()
    assert host.calls == []
    assert not any(isinstance(event, RuntimeToolRequestEvent) for event in first)
    assert not any(isinstance(event, RuntimeToolRequestEvent) for event in second)
    assert first[-1].kind.value == "completed"
    assert second[-1].kind.value == "completed"


def test_tool_observation_names_are_bounded_and_identifier_shaped() -> None:
    assert _safe_tool_observation_name("Agent") == "Agent"
    assert _safe_tool_observation_name("Agent tool") is None
    assert _safe_tool_observation_name("Agent/secret") is None
    assert _safe_tool_observation_name("A" * 129) is None
    assert _safe_tool_observation_name(None) is None


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


def test_billing_retirement_does_not_restart_runtime_session() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("unknown", clients)
        host = _Host()

        first_events = await _collect(runtime, _request(), host)
        second_events = await _collect(runtime, _request(), host)
        await runtime.close()
        return first_events, second_events, clients

    first_events, second_events, clients = asyncio.run(scenario())

    assert first_events[-1].kind.value == "failed"
    assert first_events[-1].failure.code == "claude_subscription_billing_blocked"
    assert second_events[-1].kind.value == "failed"
    assert second_events[-1].failure.code == "session_closed"
    assert len(clients) == 1
    assert clients[0].queries == ["hello runtime"]


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


def test_cancellation_is_polled_during_sustained_projection_stream() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("sustained_stream", clients)
        host = _ProjectionCountCancellationHost(cancel_after_projections=2)
        events = []
        try:
            async def collect() -> None:
                async for event in runtime.run_turn(_request(), host):
                    events.append(event)
                    if event.kind.value == "content":
                        host.projection_count += 1

            await asyncio.wait_for(collect(), timeout=0.5)
        finally:
            await runtime.close()
        return events, host, clients[0]

    events, host, client = asyncio.run(scenario())

    terminal_kinds = {"completed", "cancelled", "failed"}
    assert host.cancelled_projection_count is not None
    assert host.cancelled_projection_count >= 2
    assert len([event for event in events if event.kind.value in terminal_kinds]) == 1
    assert events[-1].kind.value == "cancelled"
    assert client.interrupted == 1
    assert client.disconnected == 1


def test_mid_stream_interrupt_breaks_and_discards_tail() -> None:
    async def scenario():
        clients: list[_InterruptThenSuccessClient] = []
        runtime = ClaudeAgentSDKRuntime(
            auth_probe=lambda: SimpleNamespace(
                allowed=True, category="subscription_oauth"
            ),
            sdk_module=_interrupt_then_success_sdk(clients),
            cwd="/synthetic/workspace",
            parent_env={},
        )
        host = _CancelOnceAfterContentHost()

        first_events = []
        async for event in runtime.run_turn(_request(), host):
            first_events.append(event)
            if event.kind.value == "content":
                host.content_observed = True

        second_events = await _collect(runtime, _request(), host)
        await runtime.close()
        return first_events, second_events, clients

    first_events, second_events, clients = asyncio.run(scenario())

    terminal_kinds = {"completed", "cancelled", "failed"}
    assert [event.kind.value for event in first_events] == ["content", "cancelled"]
    assert first_events[0].text == "partial interrupted turn"
    assert sum(event.kind.value in terminal_kinds for event in first_events) == 1
    assert first_events[-1].kind.value == "cancelled"
    assert all(
        getattr(event, "text", None) != "stale interrupted tail"
        for event in first_events + second_events
    )

    assert second_events[-1].kind.value == "completed"
    assert second_events[-1].result["text"] == "fresh second turn"
    assert sum(event.kind.value == "usage" for event in second_events) == 1
    assert sum(event.kind.value in terminal_kinds for event in second_events) == 1
    assert clients[0].interrupted == 1
    assert clients[0].disconnected == 1
    assert clients[1].interrupted == 0
    assert clients[1].disconnected == 1


def test_successful_turn_then_cancelled_turn_reuses_current_resume_on_replacement() -> None:
    async def scenario():
        clients: list[_SuccessThenInterruptThenSuccessClient] = []
        runtime = ClaudeAgentSDKRuntime(
            auth_probe=lambda: SimpleNamespace(
                allowed=True, category="subscription_oauth"
            ),
            sdk_module=_success_then_interrupt_then_success_sdk(clients),
            cwd="/synthetic/workspace",
            parent_env={},
        )
        host = _ArmableContentCancellationHost()
        first_events = await _collect(
            runtime, _request(correlation_id="turn-a"), host
        )
        state = next(
            event.state
            for event in first_events
            if event.kind.value == "session_state"
        )
        assert dict(state.state) == {"external_session_id": "synthetic-turn-a"}
        host.arm()
        second_events = []
        async for event in runtime.run_turn(
            _request(state=state, correlation_id="turn-b"), host
        ):
            second_events.append(event)
            if event.kind.value == "content":
                host.content_observed = True
        host.disarm()
        third_events = await _collect(
            runtime, _request(state=state, correlation_id="turn-c"), host
        )
        await runtime.close()
        return first_events, second_events, third_events, clients

    first_events, second_events, third_events, clients = asyncio.run(scenario())

    terminal_kinds = {"completed", "cancelled", "failed"}
    assert first_events[-1].kind.value == "completed"
    assert first_events[-1].result["text"] == "turn A"
    assert [event.kind.value for event in second_events] == [
        "content",
        "cancelled",
    ]
    assert third_events[-1].kind.value == "completed"
    assert third_events[-1].result["text"] == "turn C"
    assert clients[1].options.fields["resume"] == "synthetic-turn-a"
    assert clients[0].interrupted == 1
    assert clients[1].interrupted == 0
    assert all(
        sum(event.kind.value in terminal_kinds for event in events) == 1
        for events in (first_events, second_events, third_events)
    )
    assert all(
        getattr(event, "text", None) != "stale cancelled tail"
        for events in (first_events, second_events, third_events)
        for event in events
    )


def test_pre_set_interrupt_event_honored_then_next_turn_runs() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("success", clients)
        host = _PreSetThenContinueHost()

        first_events = await _collect(runtime, _request(), host)
        second_events = await _collect(runtime, _request(), host)
        await runtime.close()
        return first_events, second_events, clients

    first_events, second_events, clients = asyncio.run(scenario())

    assert [event.kind.value for event in first_events] == ["cancelled"]
    assert [event.kind.value for event in second_events][-1] == "completed"
    assert second_events[-1].result["text"] == "hello"
    assert clients[0].interrupted == 0
    assert clients[0].disconnected == 1
    assert clients[0].queries == ["hello runtime"]


def test_in_loop_cancellation_probe_failure_drains_projection_then_fails_closed() -> None:
    class _FailAfterContentHost(_Host):
        content_observed = False
        probe_after_content = False

        def cancellation_requested(self) -> bool:
            self.cancel_checks += 1
            if self.content_observed:
                self.probe_after_content = True
                raise RuntimeError("synthetic cancellation probe failure")
            return False

    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("cancellation_probe_failure", clients)
        host = _FailAfterContentHost()
        events = []
        async for event in runtime.run_turn(_request(), host):
            events.append(event)
            if event.kind.value == "content":
                host.content_observed = True
        await runtime.close()
        return events, host, clients[0]

    events, host, client = asyncio.run(scenario())

    assert [event.kind.value for event in events] == ["content", "failed"]
    assert events[0].text == "queued before probe failure"
    assert events[-1].failure.code == "claude_runtime_cancellation_unavailable"
    assert events[-1].failure.phase.value == "after_visible_output"
    assert host.probe_after_content is True
    assert client.interrupted == 1
    assert client.disconnected == 1


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


def test_model_switch_requires_a_new_runtime_and_preserves_tool_schema() -> None:
    async def scenario():
        first_clients: list[_Client] = []
        first_runtime = _runtime("success", first_clients)
        host = _Host()
        schemas = (_tool_schema(),)

        first = await _collect(first_runtime, _request(tools=schemas), host)
        switched_request = build_runtime_turn_request(
            provider="claude-agent-sdk",
            model="claude-fable-synthetic-switched",
            api_mode="agent_runtime",
            messages=({"role": "user", "content": "hello runtime"},),
            prompt_snapshot="stable system prompt",
            tool_schemas=schemas,
            correlation_id="synthetic-model-switch",
        )
        fenced = await _collect(first_runtime, switched_request, host)
        await first_runtime.close()

        second_clients: list[_Client] = []
        second_runtime = _runtime("success", second_clients)
        recovered = await _collect(second_runtime, switched_request, _Host())
        await second_runtime.close()

        assert first[-1].kind.value == "completed"
        assert [event.kind.value for event in fenced] == ["failed"]
        assert fenced[0].failure.code == "claude_runtime_session_contract_changed"
        assert recovered[-1].kind.value == "completed"
        assert first_clients[0].queries == ["hello runtime"]
        assert second_clients[0].queries == ["hello runtime"]
        assert first_clients[0].options.fields["model"] == "claude-fable-5"
        assert (
            second_clients[0].options.fields["model"]
            == "claude-fable-synthetic-switched"
        )
        assert (
            first_clients[0].options.fields["allowed_tools"]
            == second_clients[0].options.fields["allowed_tools"]
            == ["mcp__hermes-tools__pwd"]
        )

    asyncio.run(scenario())


def test_compaction_retry_keeps_mutation_exactly_once() -> None:
    async def scenario():
        clients: list[_Client] = []
        runtime = _runtime("compaction_tool_success", clients)
        host = _Host()

        result = await _collect_runtime_turn(
            runtime,
            _request(tools=(_tool_schema(),)),
            host,
            descriptor=build_runtime_descriptor(),
        )
        await runtime.close()

        assert [event.phase for event in host.compaction] == [
            RuntimeCompactionPhase.STARTED,
            RuntimeCompactionPhase.COMPLETED,
        ]
        assert result.terminal.kind.value == "completed"
        assert host.calls == [("pwd", {"path": "."})]
        assert not any(
            isinstance(event, RuntimeToolRequestEvent) for event in result.events
        )
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

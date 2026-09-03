from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from types import ModuleType

from hermes_claude_agent_sdk.configuration import SDKSessionConfiguration
from hermes_claude_agent_sdk.compaction import SessionCompactionPhase
from hermes_claude_agent_sdk.sdk_session import SDKSession, SessionOutcome


SDK_IMPORTED_DURING_SESSION_IMPORT = "claude_agent_sdk" in sys.modules


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, object]


@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list[object]
    model: str = "claude-fable-synthetic"
    parent_tool_use_id: str | None = None


@dataclass
class ServerToolUseBlock:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class TaskStartedMessage:
    subtype: str = "task_started"
    data: dict[str, object] | None = None


@dataclass
class StreamEvent:
    event: dict[str, object]
    parent_tool_use_id: str | None = None


@dataclass
class ResultMessage:
    subtype: str = "success"
    duration_ms: int = 1
    duration_api_ms: int = 1
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "synthetic-session-next"
    result: str | None = "hello"
    usage: dict[str, int] | None = None
    total_cost_usd: float | None = None
    terminal_reason: str | None = "completed"
    api_error_status: int | None = None
    deferred_tool_use: object | None = None


class _Options:
    def __init__(self, **fields: object) -> None:
        self.fields = fields


class _HookMatcher:
    def __init__(self, *, hooks) -> None:
        self.hooks = hooks


class _FakeClient:
    def __init__(self, *, options: object, scripts: list[list[object]]) -> None:
        self.options = options
        self.scripts = scripts
        self.connected = 0
        self.disconnected = 0
        self.interrupted = 0
        self.receive_calls = 0
        self.queries: list[str] = []
        self.active_queries = 0
        self.max_active_queries = 0
        self._messages: asyncio.Queue[object] = asyncio.Queue()
        self._closed = False

    async def connect(self) -> None:
        self.connected += 1

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        self.active_queries += 1
        self.max_active_queries = max(self.max_active_queries, self.active_queries)
        script = self.scripts.pop(0)
        for message in script:
            await self._messages.put(message)
        self.active_queries -= 1

    async def receive_messages(self):
        self.receive_calls += 1
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


_END = object()


def _sdk(client_box: list[_FakeClient], scripts: list[list[object]]) -> ModuleType:
    sdk = ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = _Options
    sdk.HookMatcher = _HookMatcher

    def make_client(*, options: object) -> _FakeClient:
        client = _FakeClient(options=options, scripts=scripts)
        client_box.append(client)
        return client

    sdk.ClaudeSDKClient = make_client
    return sdk


def _configuration(**updates: object) -> SDKSessionConfiguration:
    values = {
        "cwd": "/synthetic/workspace",
        "model": "claude-fable-synthetic",
        "parent_env": {"ANTHROPIC_API_KEY": "synthetic-secret-not-real"},
    }
    values.update(updates)
    return SDKSessionConfiguration.create(**values)


def test_import_and_configuration_do_not_import_sdk_or_retain_parent_secret() -> None:
    configuration = _configuration()

    assert SDK_IMPORTED_DURING_SESSION_IMPORT is False
    assert configuration.env_overrides == (
        ("ANTHROPIC_API_KEY", ""),
        ("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK", "1"),
    )
    assert "synthetic-secret-not-real" not in repr(configuration)


def test_bounded_turn_timeout_interrupts_and_retires_client() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=0.01),
            sdk_module=_sdk(clients, [[]]),
        )

        result = await session.run_turn("bounded wait")
        await session.close()

        assert result.outcome is SessionOutcome.TIMED_OUT
        assert result.error_code == "sdk_turn_timeout"
        assert clients[0].interrupted == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_missing_public_compaction_hook_fails_before_client_creation() -> None:
    async def scenario() -> None:
        constructed = 0

        def client_factory(*, options):
            nonlocal constructed
            constructed += 1
            raise AssertionError("client must not be constructed")

        sdk = ModuleType("claude_agent_sdk")
        sdk.ClaudeAgentOptions = _Options
        session = SDKSession(
            _configuration(),
            sdk_module=sdk,
            client_factory=client_factory,
        )

        result = await session.run_turn("missing hook support")
        await session.close()

        assert result.outcome is SessionOutcome.FAILED
        assert result.error_code == "sdk_start_failed"
        assert constructed == 0

    asyncio.run(scenario())


def test_turn_timeout_is_one_deadline_not_reset_by_stream_activity() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=0.02),
            sdk_module=_sdk(clients, [[]]),
        )

        turn = asyncio.create_task(session.run_turn("bounded active stream"))
        while not clients or not clients[0].queries:
            await asyncio.sleep(0)

        async def keep_stream_active() -> None:
            for index in range(12):
                await asyncio.sleep(0.005)
                await clients[0]._messages.put(
                    AssistantMessage([TextBlock(f"partial-{index}")])
                )

        producer = asyncio.create_task(keep_stream_active())
        # The outer wait is only a deadlock guard; the asserted 20 ms
        # SDKSession deadline remains the behavior under test.
        result = await asyncio.wait_for(turn, 2.0)
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await session.close()

        assert result.outcome is SessionOutcome.TIMED_OUT
        assert clients[0].interrupted == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_query_phase_timeout_has_the_same_typed_timeout_result() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        sdk = _sdk(clients, [[]])
        original_factory = sdk.ClaudeSDKClient

        def make_slow_client(*, options: object) -> _FakeClient:
            client = original_factory(options=options)

            async def slow_query(_prompt: str) -> None:
                await asyncio.sleep(1)

            client.query = slow_query
            return client

        sdk.ClaudeSDKClient = make_slow_client
        session = SDKSession(
            _configuration(turn_timeout_seconds=0.01),
            sdk_module=sdk,
        )

        result = await session.run_turn("bounded query")
        await session.close()

        assert result.outcome is SessionOutcome.TIMED_OUT
        assert result.error_code == "sdk_turn_timeout"
        assert clients[0].interrupted == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_native_shapes_fail_before_projection() -> None:
    async def scenario(message: object) -> tuple[object, list[object], _FakeClient]:
        clients: list[_FakeClient] = []
        projections: list[object] = []
        session = SDKSession(
            _configuration(),
            sdk_module=_sdk(clients, [[message]]),
        )
        result = await session.run_turn(
            "reject native",
            on_projection=lambda projection: projections.append(projection),
        )
        client = clients[0]
        await session.close()
        return result, projections, client

    cases = [
        AssistantMessage(
            [
                type(
                    "ToolUseBlock",
                    (),
                    {"id": "agent-1", "name": "Agent", "input": {}},
                )()
            ]
        ),
        AssistantMessage([ServerToolUseBlock("server-1", "web_search", {})]),
        AssistantMessage([TextBlock("nested")], parent_tool_use_id="parent-1"),
        TaskStartedMessage(),
        ResultMessage(
            deferred_tool_use=type(
                "DeferredToolUse",
                (),
                {"id": "deferred-1", "name": "Agent", "input": {}},
            )()
        ),
        StreamEvent(
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Agent"},
            }
        ),
        StreamEvent(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
        ),
    ]

    for message in cases:
        result, projections, client = asyncio.run(scenario(message))
        assert result.outcome is SessionOutcome.FAILED
        assert result.error_code == "sdk_native_tool_unsupported"
        assert projections == []
        assert client.queries == ["reject native"]
        assert client.interrupted == 1
        assert client.disconnected == 1


def test_exact_hermes_mcp_tool_is_allowed_before_projection() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        projections: list[object] = []
        tool = type(
            "ToolUseBlock",
            (),
            {
                "id": "tool-1",
                "name": "mcp__hermes-tools__pwd",
                "input": {"path": "."},
            },
        )()
        session = SDKSession(
            _configuration(allowed_tools=("mcp__hermes-tools__pwd",)),
            sdk_module=_sdk(
                clients,
                [[
                    SystemMessage("init", {"apiKeySource": "none"}),
                    AssistantMessage([tool]),
                    ResultMessage(result="done"),
                ]],
            ),
        )
        result = await session.run_turn(
            "allow mcp",
            on_projection=lambda projection: projections.append(projection),
        )
        await session.close()

        assert result.outcome is SessionOutcome.COMPLETE
        assert len(projections) == 3

    asyncio.run(scenario())


def test_exact_hermes_mcp_partial_stream_is_allowed() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        session = SDKSession(
            _configuration(allowed_tools=("mcp__hermes-tools__pwd",)),
            sdk_module=_sdk(
                clients,
                [[
                    SystemMessage("init", {"apiKeySource": "none"}),
                    StreamEvent(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {
                                "type": "tool_use",
                                "name": "mcp__hermes-tools__pwd",
                            },
                        }
                    ),
                    StreamEvent(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": "{}",
                            },
                        }
                    ),
                    ResultMessage(result="done"),
                ]],
            ),
        )
        result = await session.run_turn("allow stream")
        await session.close()

        assert result.outcome is SessionOutcome.COMPLETE

    asyncio.run(scenario())


def test_idle_native_shape_fails_before_next_query_or_projection() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        projections: list[object] = []
        session = SDKSession(
            _configuration(),
            sdk_module=_sdk(
                clients,
                [[
                    SystemMessage("init", {"apiKeySource": "none"}),
                    AssistantMessage([TextBlock("done")]),
                    ResultMessage(),
                ]],
            ),
        )
        first = await session.run_turn("first", on_projection=projections.append)
        await clients[0]._messages.put(
            AssistantMessage(
                [
                    type(
                        "ToolUseBlock",
                        (),
                        {"id": "agent-2", "name": "Agent", "input": {}},
                    )()
                ]
            )
        )
        for _ in range(20):
            await asyncio.sleep(0)
        projection_count = len(projections)
        second = await session.run_turn("second", on_projection=projections.append)
        await session.close()

        assert first.outcome is SessionOutcome.COMPLETE
        assert second.outcome is SessionOutcome.FAILED
        assert second.error_code == "sdk_native_tool_unsupported"
        assert len(projections) == projection_count
        assert clients[0].queries == ["first"]

    asyncio.run(scenario())


def test_text_turn_uses_public_options_one_reader_projection_and_exact_close() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        sdk = _sdk(
            clients,
            [[
                SystemMessage("init", {"apiKeySource": "none"}),
                AssistantMessage([TextBlock("hello")]),
                ResultMessage(usage={"input_tokens": 2, "output_tokens": 3}),
            ]],
        )
        projections = []
        billing = []
        timeline = []

        def record_projection(projection) -> None:
            projections.append(projection)
            timeline.append(("projection", projection.is_result))

        def record_billing(decision) -> None:
            billing.append(decision)
            timeline.append(("billing", decision.allowed))

        session = SDKSession(
            _configuration(),
            sdk_module=sdk,
            on_projection=record_projection,
            on_billing_decision=record_billing,
        )

        result = await session.run_turn("synthetic prompt")
        await session.close()
        await session.close()

        client = clients[0]
        fields = client.options.fields
        assert fields["model"] == "claude-fable-synthetic"
        assert fields["cwd"] == "/synthetic/workspace"
        assert fields["env"] == {
            "ANTHROPIC_API_KEY": "",
            "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
        }
        assert fields["system_prompt"] == ""
        assert fields["setting_sources"] == []
        assert fields["tools"] == []
        assert set(fields["hooks"]) == {"PreCompact"}
        assert client.queries == ["synthetic prompt"]
        assert client.receive_calls == 1
        assert client.disconnected == 1
        assert result.outcome is SessionOutcome.COMPLETE
        assert result.final_text == "hello"
        assert result.state_update.external_session_id == "synthetic-session-next"
        assert len(projections) == 3
        assert billing[0].allowed is True
        assert timeline[-2:] == [("billing", True), ("projection", True)]

    asyncio.run(scenario())


def test_compaction_hook_boundary_and_turn_projection_are_ordered() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        events = []
        projections = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=1),
            sdk_module=_sdk(clients, [[]]),
        )

        turn = asyncio.create_task(
            session.run_turn(
                "native compaction",
                on_projection=projections.append,
                on_compaction_event=events.append,
            )
        )
        while not clients or not clients[0].queries:
            await asyncio.sleep(0)

        hook = clients[0].options.fields["hooks"]["PreCompact"][0].hooks[0]
        assert await hook({"trigger": "auto"}, None, None) == {}
        await clients[0]._messages.put(
            SystemMessage(
                "compact_boundary",
                {"compact_metadata": {"trigger": "auto"}},
            )
        )
        await clients[0]._messages.put(
            SystemMessage("init", {"apiKeySource": "none"})
        )
        await clients[0]._messages.put(ResultMessage())

        result = await turn
        await session.close()

        assert result.outcome is SessionOutcome.COMPLETE
        assert [event.phase for event in events] == [
            SessionCompactionPhase.STARTED,
            SessionCompactionPhase.COMPLETED,
        ]
        # The boundary is lifecycle-only.  It is the first SDK message in
        # this script and therefore produces an empty public content projection.
        assert projections[0].events == ()

    asyncio.run(scenario())


def test_missing_compact_boundary_trips_bounded_watchdog_and_retires_client() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        events = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=1),
            sdk_module=_sdk(clients, [[]]),
            compaction_watchdog_seconds=0.01,
        )

        turn = asyncio.create_task(
            session.run_turn(
                "bounded compaction",
                on_compaction_event=events.append,
            )
        )
        while not clients or not clients[0].queries:
            await asyncio.sleep(0)

        hook = clients[0].options.fields["hooks"]["PreCompact"][0].hooks[0]
        await hook({"trigger": "auto"}, None, None)
        # The outer wait is only a deadlock guard; the asserted 10 ms
        # compaction watchdog remains the behavior under test.
        result = await asyncio.wait_for(turn, 2.0)
        await session.close()

        assert result.outcome is SessionOutcome.FAILED
        assert result.error_code == "sdk_compaction_watchdog"
        assert [event.phase for event in events] == [
            SessionCompactionPhase.STARTED,
            SessionCompactionPhase.WATCHDOG,
        ]
        assert clients[0].interrupted == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_native_compaction_suspends_then_restamps_the_turn_deadline() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        events = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=0.02),
            sdk_module=_sdk(clients, [[]]),
            # This test exercises turn-deadline suspension/restamping, not
            # watchdog expiry.  Keep the watchdog well outside scheduler
            # jitter on loaded CI runners; the dedicated watchdog test above
            # retains its intentionally tiny behavioral deadline.
            compaction_watchdog_seconds=30.0,
        )

        turn = asyncio.create_task(
            session.run_turn(
                "native compaction deadline",
                on_compaction_event=events.append,
            )
        )
        while not clients or not clients[0].queries:
            await asyncio.sleep(0)

        hook = clients[0].options.fields["hooks"]["PreCompact"][0].hooks[0]
        await hook({"trigger": "auto"}, None, None)
        await asyncio.sleep(0.03)
        await clients[0]._messages.put(
            SystemMessage(
                "compact_boundary",
                {"compact_metadata": {"trigger": "auto"}},
            )
        )
        await clients[0]._messages.put(
            SystemMessage("init", {"apiKeySource": "none"})
        )
        await clients[0]._messages.put(ResultMessage())

        # Outer deadlock guard only.  The asserted turn deadline remains 20 ms.
        result = await asyncio.wait_for(turn, 2.0)
        await session.close()

        assert result.outcome is SessionOutcome.COMPLETE
        assert [event.phase for event in events] == [
            SessionCompactionPhase.STARTED,
            SessionCompactionPhase.COMPLETED,
        ]
        assert clients[0].interrupted == 0

    asyncio.run(scenario())


def test_cancelling_active_compaction_emits_failed_exactly_once() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        events = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=1),
            sdk_module=_sdk(clients, [[]]),
        )

        turn = asyncio.create_task(
            session.run_turn(
                "cancel active compaction",
                on_compaction_event=events.append,
            )
        )
        while not clients or not clients[0].queries:
            await asyncio.sleep(0)

        hook = clients[0].options.fields["hooks"]["PreCompact"][0].hooks[0]
        await hook({"trigger": "auto"}, None, None)
        await session.cancel()
        result = await turn
        await session.close()

        assert result.outcome is SessionOutcome.CANCELLED
        assert [event.phase for event in events] == [
            SessionCompactionPhase.STARTED,
            SessionCompactionPhase.FAILED,
        ]
        assert clients[0].interrupted == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_resume_is_only_passed_through_public_option_field() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        sdk = _sdk(
            clients,
            [[SystemMessage("init", {"apiKeySource": "none"}), ResultMessage()]],
        )
        session = SDKSession(
            _configuration(resume_external_session_id="synthetic-resume-id"),
            sdk_module=sdk,
        )

        result = await session.run_turn("continue conversation")
        await session.close()

        assert clients[0].options.fields["resume"] == "synthetic-resume-id"
        assert result.state_update.external_session_id == "synthetic-session-next"
        assert clients[0].queries == ["continue conversation"]

    asyncio.run(scenario())


def test_unknown_or_metered_billing_blocks_terminal_success() -> None:
    async def run(script: list[object]):
        clients: list[_FakeClient] = []
        decisions = []
        projections = []
        session = SDKSession(
            _configuration(),
            sdk_module=_sdk(clients, [script]),
            on_projection=projections.append,
            on_billing_decision=decisions.append,
        )
        result = await session.run_turn("billing")
        await session.close()
        return result, decisions, projections, clients[0]

    unknown, unknown_decisions, unknown_projections, unknown_client = asyncio.run(
        run([ResultMessage()])
    )
    metered, metered_decisions, _, metered_client = asyncio.run(
        run([
            SystemMessage("init", {"apiKeySource": "api_key"}),
            ResultMessage(),
        ])
    )

    assert unknown.outcome is SessionOutcome.BILLING_BLOCKED
    assert unknown_decisions[0].block_reason.value == "unknown_evidence"
    assert unknown_projections == []
    assert unknown_client.disconnected == 1
    assert metered.outcome is SessionOutcome.BILLING_BLOCKED
    assert metered_decisions[0].block_reason.value == "api_key_source"
    assert metered_client.interrupted == 1


def test_cancel_interrupts_retires_client_and_classifies_once() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        session = SDKSession(
            _configuration(turn_timeout_seconds=5),
            sdk_module=_sdk(clients, [[]]),
        )

        turn = asyncio.create_task(session.run_turn("wait"))
        while not clients or not clients[0].queries:
            await asyncio.sleep(0)
        cancel_result = await session.cancel()
        result = await turn
        await session.close()

        assert cancel_result is SessionOutcome.CANCELLED
        assert result.outcome is SessionOutcome.CANCELLED
        assert clients[0].interrupted == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_interrupted_turn_consumes_its_own_result() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        session = SDKSession(
            _configuration(),
            sdk_module=_sdk(
                clients,
                [
                    [
                        SystemMessage("init", {"apiKeySource": "none"}),
                        AssistantMessage([TextBlock("interrupted partial")]),
                        ResultMessage(
                            result="interrupted tail",
                            terminal_reason="aborted_streaming",
                            is_error=True,
                            api_error_status=429,
                        ),
                    ],
                    [
                        SystemMessage("init", {"apiKeySource": "none"}),
                        AssistantMessage([TextBlock("next turn")]),
                        ResultMessage(result="next turn"),
                    ],
                ],
            ),
        )

        first = await session.run_turn("interrupting turn")
        second = await session.run_turn("next turn")
        await session.close()

        assert first.outcome is SessionOutcome.CANCELLED
        assert second.outcome is SessionOutcome.COMPLETE
        assert second.final_text == "next turn"
        assert clients[0].queries == ["interrupting turn", "next turn"]
        assert clients[0].interrupted == 0
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_concurrent_turns_are_serialized_on_one_reader() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        scripts = [
            [SystemMessage("init", {"apiKeySource": "none"}), ResultMessage(result="one")],
            [SystemMessage("init", {"apiKeySource": "none"}), ResultMessage(result="two")],
        ]
        session = SDKSession(_configuration(), sdk_module=_sdk(clients, scripts))

        first, second = await asyncio.gather(
            session.run_turn("one"), session.run_turn("two")
        )
        await session.close()

        assert [first.final_text, second.final_text] == ["one", "two"]
        assert clients[0].max_active_queries == 1
        assert clients[0].receive_calls == 1

    asyncio.run(scenario())


def test_post_terminal_sdk_output_is_a_protocol_failure_without_background_delivery() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        session = SDKSession(
            _configuration(),
            sdk_module=_sdk(
                clients,
                [[SystemMessage("init", {"apiKeySource": "none"}), ResultMessage()]],
            ),
        )

        first = await session.run_turn("parent turn")
        assert first.outcome is SessionOutcome.COMPLETE
        await clients[0]._messages.put(AssistantMessage([TextBlock("idle output")]))
        await clients[0]._messages.put(ResultMessage(result="idle output"))
        for _ in range(20):
            await asyncio.sleep(0)
        second = await session.run_turn("next turn")

        assert second.outcome is SessionOutcome.FAILED
        assert second.error_code == "sdk_post_terminal_output"
        assert clients[0].queries == ["parent turn"]
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_terminal_error_turn_keeps_the_compatible_sdk_session_warm() -> None:
    async def scenario() -> None:
        clients: list[_FakeClient] = []
        scripts = [
            [
                SystemMessage("init", {"apiKeySource": "none"}),
                ResultMessage(result="bounded failure", is_error=True),
            ],
            [
                SystemMessage("init", {"apiKeySource": "none"}),
                ResultMessage(result="recovered"),
            ],
        ]
        session = SDKSession(_configuration(), sdk_module=_sdk(clients, scripts))

        failed = await session.run_turn("fail this turn")
        recovered = await session.run_turn("recover on the same session")
        await session.close()

        assert failed.outcome is SessionOutcome.FAILED
        assert failed.error_code == "sdk_result_failed"
        assert recovered.outcome is SessionOutcome.COMPLETE
        assert recovered.final_text == "recovered"
        assert len(clients) == 1
        assert clients[0].connected == 1
        assert clients[0].queries == [
            "fail this turn",
            "recover on the same session",
        ]
        assert clients[0].disconnected == 1

    asyncio.run(scenario())


def test_terminal_error_uses_only_safe_structured_sdk_discriminators() -> None:
    async def scenario() -> None:
        cases = (
            ({"api_error_status": 401}, "sdk_api_auth_401", False),
            ({"api_error_status": 402}, "sdk_api_billing_402", False),
            ({"api_error_status": 408}, "sdk_api_timeout_408", True),
            ({"api_error_status": 429}, "sdk_api_rate_limit_429", True),
            ({"api_error_status": 503}, "sdk_api_overloaded_503", True),
            ({"api_error_status": 529}, "sdk_api_overloaded_529", True),
            ({"api_error_status": 500}, "sdk_api_server_error_500", True),
            ({"api_error_status": 404}, "sdk_api_error_404", False),
            (
                {
                    "api_error_status": 429,
                    "terminal_reason": "max_turns",
                    "subtype": "error_during_execution",
                },
                "sdk_api_rate_limit_429",
                True,
            ),
            (
                {"api_error_status": True, "terminal_reason": "api_error"},
                "sdk_terminal_api_error",
                False,
            ),
            (
                {"api_error_status": "429", "terminal_reason": "max_turns"},
                "sdk_terminal_max_turns",
                False,
            ),
            (
                {"terminal_reason": "unknown", "subtype": "error_max_turns"},
                "sdk_result_error_max_turns",
                False,
            ),
            (
                {
                    "terminal_reason": "unknown",
                    "subtype": "error_during_execution",
                },
                "sdk_result_error_during_execution",
                False,
            ),
            (
                {"terminal_reason": "untrusted reason", "subtype": "untrusted/type"},
                "sdk_result_failed",
                False,
            ),
        )
        clients: list[_FakeClient] = []
        scripts = [
            [
                SystemMessage("init", {"apiKeySource": "none"}),
                ResultMessage(result="private failure text", is_error=True, **fields),
            ]
            for fields, _code, _retryable in cases
        ]
        session = SDKSession(_configuration(), sdk_module=_sdk(clients, scripts))

        results = [
            await session.run_turn(f"case-{index}")
            for index in range(len(cases))
        ]
        await session.close()

        assert [
            (result.error_code, result.retryable) for result in results
        ] == [(code, retryable) for _fields, code, retryable in cases]
        assert all(result.outcome is SessionOutcome.FAILED for result in results)
        assert all(result.final_text is None for result in results)
        assert all(
            "private failure text" not in (result.error_code or "")
            for result in results
        )

    asyncio.run(scenario())


def test_sdk_stream_without_a_terminal_result_fails_closed() -> None:
    class _EndsWithoutTerminalClient(_FakeClient):
        async def receive_messages(self):
            self.receive_calls += 1
            yield SystemMessage("init", {"apiKeySource": "none"})

    async def scenario() -> None:
        clients: list[_EndsWithoutTerminalClient] = []
        sdk = ModuleType("claude_agent_sdk")
        sdk.ClaudeAgentOptions = _Options
        sdk.HookMatcher = _HookMatcher

        def make_client(*, options: object) -> _EndsWithoutTerminalClient:
            client = _EndsWithoutTerminalClient(options=options, scripts=[[]])
            clients.append(client)
            return client

        sdk.ClaudeSDKClient = make_client
        session = SDKSession(_configuration(), sdk_module=sdk)

        result = await session.run_turn("terminal result is required")
        await session.close()

        assert result.outcome is SessionOutcome.FAILED
        assert result.error_code == "sdk_stream_ended"
        assert len(clients) == 1
        assert clients[0].queries == ["terminal result is required"]
        assert clients[0].receive_calls == 1
        assert clients[0].disconnected == 1

    asyncio.run(scenario())

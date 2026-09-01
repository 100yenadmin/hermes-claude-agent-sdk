"""Deterministic, offline approval-followthrough proof for the SDK runtime.

This module is intentionally a test seam, not a runtime feature.  It supplies
small Claude SDK-shaped doubles and one runner that puts an installed plugin
through Hermes' public ``run_runtime_sync`` and ``HermesRuntimeHostServices``
surfaces.  The host still owns tool middleware, approval, terminal execution,
and usage persistence; no provider client or credential lookup is used.

Only normalized lifecycle facts leave the runner.  Provider messages,
terminal output, tool arguments, and exceptions are never returned or logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, AsyncIterator, Callable, Iterator, Mapping


SYNTHETIC_SESSION_ID = "synthetic-approval-session"
SYNTHETIC_TASK_ID = "synthetic-approval-task"
SYNTHETIC_CORRELATION_ID = "synthetic-approval-correlation"
SYNTHETIC_MODEL = "claude-fable-5"
SYNTHETIC_INPUT_TOKENS = 2
SYNTHETIC_OUTPUT_TOKENS = 3
EXPECTED_APPROVALS = ("approved", "denied", "approved")
EXPECTED_TOOL_OUTCOMES = ("ok", "blocked", "ok")


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, object]


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class AssistantMessage:
    content: list[object]
    model: str = SYNTHETIC_MODEL


@dataclass
class ResultMessage:
    result: str = "synthetic complete"
    usage: dict[str, int] | None = None
    session_id: str = "synthetic-external-session"
    is_error: bool = False
    terminal_reason: str = "completed"
    subtype: str = "success"
    total_cost_usd: float | None = None
    num_turns: int = 1
    duration_ms: int = 1
    duration_api_ms: int = 1


class _Options:
    def __init__(self, **fields: object) -> None:
        self.fields = fields


class _HookMatcher:
    def __init__(self, *, hooks: object) -> None:
        self.hooks = hooks


_END = object()


class _Client:
    """A no-I/O SDK client that drives three MCP calls in one turn."""

    def __init__(self, *, options: _Options) -> None:
        self.options = options
        self.connected = 0
        self.disconnected = 0
        self.interrupted = 0
        self.query_count = 0
        self._closed = False
        self._messages: asyncio.Queue[object] = asyncio.Queue()

    async def connect(self) -> None:
        self.connected += 1

    async def query(self, prompt: str) -> None:
        # ``prompt`` is intentionally not retained: it is provider-bound data.
        self.query_count += 1
        server = self.options.fields["mcp_servers"]["hermes-tools"]
        handler = next(
            tool["handler"]
            for tool in server["tools"]
            if tool["name"] == "terminal"
        )
        for index in range(1, 4):
            await handler({"command": "pwd"})
            await self._messages.put(
                AssistantMessage(
                    [
                        ToolUseBlock(
                            f"synthetic-tool-{index:04d}",
                            "terminal",
                            {"command": "pwd"},
                        )
                    ]
                )
            )
        await self._messages.put(SystemMessage("init", {"apiKeySource": "none"}))
        await self._messages.put(
            ResultMessage(
                usage={
                    "input_tokens": SYNTHETIC_INPUT_TOKENS,
                    "output_tokens": SYNTHETIC_OUTPUT_TOKENS,
                }
            )
        )

    async def receive_messages(self) -> AsyncIterator[object]:
        while not self._closed:
            message = await self._messages.get()
            if message is _END:
                return
            yield message
            if isinstance(message, ResultMessage):
                # This one-turn fixture ends its reader naturally so no task
                # is left pending when the synchronous host runner closes
                # its event loop.
                return

    async def interrupt(self) -> None:
        self.interrupted += 1
        self._closed = True
        await self._messages.put(_END)

    async def disconnect(self) -> None:
        self.disconnected += 1
        self._closed = True
        await self._messages.put(_END)


def synthetic_sdk() -> ModuleType:
    """Return the injected SDK module used by both focused and wheel tests."""

    sdk = ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = _Options
    sdk.HookMatcher = _HookMatcher

    def create_client(*, options: _Options) -> _Client:
        return _Client(options=options)

    def tool(name: str, description: str, input_schema: Mapping[str, Any]):
        def decorate(handler: Callable[..., Any]) -> dict[str, Any]:
            return {
                "name": name,
                "description": description,
                "input_schema": input_schema,
                "handler": handler,
            }

        return decorate

    def create_sdk_mcp_server(*, name: str, version: str, tools: list[Any]) -> dict[str, Any]:
        return {"name": name, "version": version, "tools": tools}

    sdk.ClaudeSDKClient = create_client
    sdk.tool = tool
    sdk.create_sdk_mcp_server = create_sdk_mcp_server
    return sdk


@contextlib.contextmanager
def _hermetic_environment() -> Iterator[Path]:
    """Bind a temporary home and scrub ambient provider configuration."""

    # The host's macOS login-shell bootstrap can finish writing its interpreter
    # cache just after the command returns.  Cleanup is best-effort for this
    # disposable root; all fixture state remains confined to the OS temp dir.
    with tempfile.TemporaryDirectory(
        prefix="hermes-sdk-approval-", ignore_cleanup_errors=True
    ) as root_value:
        root = Path(root_value)
        home = root / "home"
        hermes_home = root / "hermes-home"
        home.mkdir()
        hermes_home.mkdir()
        names = (
            "HOME",
            "HERMES_HOME",
            "HERMES_INTERACTIVE",
            "HERMES_GATEWAY_SESSION",
            "HERMES_SESSION_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "PATH",
            "PYTHONNOUSERSITE",
            "PYTHONPYCACHEPREFIX",
            "LC_ALL",
            "TIRITH_ENABLED",
        )
        prior = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ.pop(name, None)
            os.environ.update(
                {
                    "HOME": str(home),
                    "HERMES_HOME": str(hermes_home),
                    "HERMES_INTERACTIVE": "1",
                    "HERMES_SESSION_KEY": SYNTHETIC_SESSION_ID,
                    "PATH": os.defpath,
                    "PYTHONNOUSERSITE": "1",
                    "LC_ALL": "C.UTF-8",
                    # Keep the harmless local command strictly offline.  A
                    # missing Tirith binary otherwise triggers its optional
                    # release auto-download before ``pwd`` executes.
                    "TIRITH_ENABLED": "0",
                }
            )
            yield root
        finally:
            for name, value in prior.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class _Lifecycle:
    """Observer and approval callback state with normalized-only output."""

    def __init__(self) -> None:
        self.approval_requests: list[str] = []
        self.approval_responses: list[str] = []
        self.tool_outcomes: list[str] = []
        self.lifecycle_trace: list[str] = []
        self._approval_index = 0
        self._tool_index = 0

    def pre_tool_call(self, **_: Any) -> dict[str, str]:
        from tools.terminal_tool import set_approval_callback

        set_approval_callback(self.approval_callback)
        self._tool_index += 1
        return {
            "action": "approve",
            "message": "synthetic approval required",
            "rule_key": f"synthetic-terminal-{self._tool_index}",
        }

    def approval_request(self, **_: Any) -> None:
        self.approval_requests.append("requested")
        self.lifecycle_trace.append("approval_requested")

    def approval_response(self, **kwargs: Any) -> None:
        choice = kwargs.get("choice")
        self.approval_responses.append(
            "approved" if choice in {"once", "session", "always"} else "denied"
        )
        self.lifecycle_trace.append(f"approval_{self.approval_responses[-1]}")

    def post_tool_call(self, **kwargs: Any) -> None:
        status = kwargs.get("status")
        error_type = kwargs.get("error_type")
        if status == "blocked" and error_type == "plugin_block":
            self.tool_outcomes.append("blocked")
        elif status in {None, "ok"}:
            self.tool_outcomes.append("ok")
        else:
            self.tool_outcomes.append("error")
        self.lifecycle_trace.append(f"tool_{self.tool_outcomes[-1]}")

    def approval_callback(self, *_args: Any, **_kwargs: Any) -> str:
        self._approval_index += 1
        return ("once", "deny", "once")[self._approval_index - 1]


def _tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Run one bounded terminal command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }


def _load_host(host_root: str) -> tuple[Any, ...]:
    root = str(Path(host_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from agent.runtime_api import RuntimeCompletedEvent, RuntimeToolRequestEvent, RuntimeUsageEvent
    from agent.runtime_dispatch import (
        HermesRuntimeHostServices,
        build_runtime_turn_request,
        run_runtime_sync,
    )
    from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager
    from hermes_state import SessionDB
    from run_agent import AIAgent

    return (
        AIAgent,
        HermesRuntimeHostServices,
        PluginContext,
        PluginManifest,
        RuntimeCompletedEvent,
        RuntimeToolRequestEvent,
        RuntimeUsageEvent,
        build_runtime_turn_request,
        run_runtime_sync,
        SessionDB,
        get_plugin_manager,
    )


def _normalized_receipt(receipt: Any) -> dict[str, Any]:
    return {
        "model": receipt.model,
        "selected_model": receipt.selected_model,
        "effective_model": receipt.effective_model,
        "canonical_model": receipt.canonical_model,
        "model_resolution": receipt.model_resolution,
        "billing_mode": receipt.billing_mode,
        "cost_status": receipt.cost_status,
        "correlation_id": receipt.correlation_id,
        "input_tokens": receipt.input_tokens,
        "output_tokens": receipt.output_tokens,
    }


def run_approval_followthrough(*, host_root: str, plugin_module: Any | None = None) -> dict[str, Any]:
    """Exercise the installed/source plugin against the real public host path."""

    with _hermetic_environment():
        (
            AIAgent,
            HermesRuntimeHostServices,
            PluginContext,
            PluginManifest,
            RuntimeCompletedEvent,
            RuntimeToolRequestEvent,
            RuntimeUsageEvent,
            build_runtime_turn_request,
            run_runtime_sync,
            SessionDB,
            get_plugin_manager,
        ) = _load_host(host_root)
        if plugin_module is None:
            plugin_module = importlib.import_module("hermes_claude_agent_sdk")

        manager = get_plugin_manager()
        plugin_context = PluginContext(
            PluginManifest(
                name="claude-agent-sdk",
                key="claude-agent-sdk",
                source="installed-wheel",
            ),
            manager,
        )
        plugin_module.register(plugin_context)

        lifecycle = _Lifecycle()
        observer_context = PluginContext(
            PluginManifest(
                name="synthetic-approval-observer",
                key="synthetic-approval-observer",
                source="test-fixture",
            ),
            manager,
        )
        registrations = [
            observer_context.register_hook("pre_tool_call", lifecycle.pre_tool_call),
            observer_context.register_hook(
                "pre_approval_request", lifecycle.approval_request
            ),
            observer_context.register_hook(
                "post_approval_response", lifecycle.approval_response
            ),
            observer_context.register_hook("post_tool_call", lifecycle.post_tool_call),
        ]

        database: Any | None = None
        agent: Any | None = None
        try:
            root = Path(os.environ["HERMES_HOME"]).parent
            database = SessionDB(db_path=root / "state.db")
            database.ensure_session(
                SYNTHETIC_SESSION_ID,
                source="synthetic",
                model=SYNTHETIC_MODEL,
            )
            agent = AIAgent(
                provider="claude-agent-sdk",
                api_mode="agent_runtime",
                model=SYNTHETIC_MODEL,
                session_id=SYNTHETIC_SESSION_ID,
                enabled_toolsets=["terminal"],
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                skip_background_review=True,
                session_db=database,
            )
            from tools.terminal_tool import set_approval_callback

            # Approval callbacks are thread-local.  Hermes invokes policy
            # hooks in bounded observer workers, so install the callback on
            # the host executor thread as the public terminal API requires.
            set_approval_callback(lifecycle.approval_callback)
            request = build_runtime_turn_request(
                provider="claude-agent-sdk",
                model=SYNTHETIC_MODEL,
                api_mode="agent_runtime",
                messages=({"role": "user", "content": "synthetic approval turn"},),
                prompt_snapshot="synthetic approval turn",
                tool_schemas=(_tool_schema(),),
                correlation_id=SYNTHETIC_CORRELATION_ID,
            )
            auth_probe_calls = 0

            def auth_probe() -> Any:
                nonlocal auth_probe_calls
                auth_probe_calls += 1
                return SimpleNamespace(
                    allowed=True,
                    category="subscription_oauth",
                )

            runtime = plugin_module.ClaudeAgentSDKRuntime(
                auth_probe=auth_probe,
                sdk_module=synthetic_sdk(),
                cwd=str(root),
                parent_env={
                    "PATH": os.defpath,
                    "HOME": os.environ["HOME"],
                    "HERMES_HOME": os.environ["HERMES_HOME"],
                },
            )

            class RecordingHost(HermesRuntimeHostServices):
                """Count public host-facade crossings without changing them."""

                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    self.execute_calls = 0
                    super().__init__(*args, **kwargs)

                async def execute_tool(
                    self, name: str, arguments: Mapping[str, Any]
                ) -> Any:
                    self.execute_calls += 1
                    return await super().execute_tool(name, arguments)

            host = RecordingHost(
                agent,
                task_id=SYNTHETIC_TASK_ID,
                runtime_id=plugin_module.RUNTIME_ID,
            )
            result = run_runtime_sync(
                runtime,
                request,
                host,
                descriptor=plugin_module.build_runtime_descriptor(),
            )
            receipts = database.list_runtime_usage_receipts(
                SYNTHETIC_SESSION_ID,
                plugin_module.RUNTIME_ID,
            )
            database.flush_token_counts()

            events = result.events
            terminal_count = sum(
                isinstance(event, RuntimeCompletedEvent) for event in events
            )
            tool_request_count = sum(
                isinstance(event, RuntimeToolRequestEvent) for event in events
            )
            usage_count = sum(isinstance(event, RuntimeUsageEvent) for event in events)
            completed = isinstance(result.terminal, RuntimeCompletedEvent)
            return {
                "status": "passed" if completed else "failed",
                "execution_path": "public_run_runtime_sync",
                "tool": "terminal",
                "approval_requests": len(lifecycle.approval_requests),
                "approval_outcomes": tuple(lifecycle.approval_responses),
                "tool_outcomes": tuple(lifecycle.tool_outcomes),
                "host_lifecycle_trace": tuple(lifecycle.lifecycle_trace),
                "host_execute_tool_calls": host.execute_calls,
                "runtime_tool_requests": tool_request_count,
                "runtime_usage_events": usage_count,
                "runtime_terminal_events": terminal_count,
                "usage_receipts": tuple(_normalized_receipt(item) for item in receipts),
                "provider_calls": 0,
                # The injected seam is exercised once; no real auth call is
                # made and no credential is read.
                "auth_calls": 0,
                "synthetic_auth_probe_calls": auth_probe_calls,
                "network_calls": 0,
                "raw_payloads": 0,
                "shared_state": "temporary_only",
                "expected_approvals": EXPECTED_APPROVALS,
                "expected_tool_outcomes": EXPECTED_TOOL_OUTCOMES,
            }
        finally:
            if agent is not None:
                agent.close()
            for registration in reversed(registrations):
                registration.dispose()
            # The plugin registrations are owned by the plugin context and
            # are disposed here so the process-local manager cannot bleed into
            # another focused assertion.
            manager.unload(plugin_context.manifest)
            if database is not None:
                database.flush_token_counts()
                database.close()
            try:
                from tools.terminal_tool import (
                    clear_session_cwd,
                    cleanup_vm,
                    set_approval_callback,
                )

                set_approval_callback(None)
                clear_session_cwd(SYNTHETIC_SESSION_ID)
                cleanup_vm(f"session:{SYNTHETIC_SESSION_ID}")
            except Exception:
                pass


def render_report(report: Mapping[str, Any]) -> str:
    """Render stable JSON suitable for a later evidence packet."""

    return json.dumps(report, sort_keys=True, separators=(",", ":"))


__all__ = [
    "EXPECTED_APPROVALS",
    "EXPECTED_TOOL_OUTCOMES",
    "render_report",
    "run_approval_followthrough",
    "synthetic_sdk",
]

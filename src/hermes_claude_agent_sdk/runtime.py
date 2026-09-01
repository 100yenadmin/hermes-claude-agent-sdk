"""AgentRuntime v1 composition over the public Claude Agent SDK adapter."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from .compatibility import API_MODES, MODEL_PREFIXES, PROVIDER_IDS, RUNTIME_ID
from .configuration import SDKSessionConfiguration
from .prompt_context import build_sdk_prompt_context
from .tool_bridge import HostToolBridge
from .turn_input import TurnInputValidationError, build_sdk_turn_input

_MAX_SYSTEM_APPEND = 32_000
_MCP_SERVER_NAME = "hermes-tools"


def _failure(code: str, message: str, phase: Any, *, replay_safe: bool) -> Any:
    from agent.runtime_api import RuntimeFailure

    return RuntimeFailure(code=code, message=message, phase=phase, replay_safe=replay_safe)


def _resume_id(request: Any) -> tuple[str | None, bool]:
    envelope = getattr(request, "session_state", None)
    if envelope is None:
        return None, True
    if (
        getattr(envelope, "runtime_id", None) != RUNTIME_ID
        or getattr(envelope, "schema_version", None) != 1
    ):
        return None, False
    state = getattr(envelope, "state", None)
    if not isinstance(state, Mapping) or set(state) - {"external_session_id"}:
        return None, False
    value = state.get("external_session_id")
    if value is None:
        return None, True
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None, False
    return value, True


class ClaudeAgentSDKRuntime:
    """Small provider-local runtime with injected offline seams for tests."""

    def __init__(
        self,
        *,
        auth_probe: Callable[[], Any] | None = None,
        sdk_module: Any | None = None,
        sdk_module_loader: Callable[[], Any] | None = None,
        client_factory: Callable[..., Any] | None = None,
        cwd: str = ".",
        parent_env: Mapping[str, object] | None = None,
        compaction_watchdog_seconds: float = 600.0,
    ) -> None:
        self._auth_probe = auth_probe
        self._sdk = sdk_module
        self._sdk_module_loader = sdk_module_loader
        self._client_factory = client_factory
        self._cwd = cwd
        self._parent_env = parent_env
        self._compaction_watchdog_seconds = compaction_watchdog_seconds
        self._auth_allowed: bool | None = None
        self._session: Any | None = None
        self._bridge: HostToolBridge | None = None
        self._host: Any | None = None
        self._session_contract: tuple[str, str, str, str, str | None] | None = None
        self._session_configuration: SDKSessionConfiguration | None = None
        self._closed = False

    async def _emit_background_result(self, result: Any) -> None:
        """Delegate exact-parent routing and delivery exclusively to the host."""

        if self._closed or self._host is None:
            return
        from agent.runtime_api import RuntimeBackgroundOutcome, RuntimeBackgroundResult
        from .sdk_session import BackgroundSessionOutcome

        outcome = (
            RuntimeBackgroundOutcome.FAILED
            if result.outcome is BackgroundSessionOutcome.FAILED
            else RuntimeBackgroundOutcome.COMPLETED
        )
        try:
            await self._host.emit_background_result(
                RuntimeBackgroundResult(content=result.content, outcome=outcome)
            )
        except Exception:
            # A sealed host binding rejects late work.  Delivery/requeue is a
            # host responsibility, so the plugin neither retries nor reroutes.
            return

    def _default_auth_probe(self) -> Any:
        module = importlib.import_module(".auth", __package__)
        return module.probe_claude_auth()

    def _check_auth(self) -> bool:
        if self._auth_allowed is not None:
            return self._auth_allowed
        try:
            result = (self._auth_probe or self._default_auth_probe)()
            category = getattr(result, "category", None)
            category = getattr(category, "value", category)
            self._auth_allowed = (
                getattr(result, "allowed", None) is True
                and category == "subscription_oauth"
            )
        except Exception:
            self._auth_allowed = False
        return self._auth_allowed

    def _new_session(self, configuration: SDKSessionConfiguration) -> Any:
        from .sdk_session import SDKSession

        return SDKSession(
            configuration,
            sdk_module=self._sdk,
            client_factory=self._client_factory,
            on_background_result=self._emit_background_result,
            compaction_watchdog_seconds=self._compaction_watchdog_seconds,
        )

    async def _load_sdk_module(self) -> Any:
        """Load the optional SDK without blocking cancellation polling."""

        if self._sdk is not None:
            return self._sdk
        if self._sdk_module_loader is None:
            return await asyncio.to_thread(
                importlib.import_module, "claude_agent_sdk"
            )
        result = self._sdk_module_loader()
        return await result if inspect.isawaitable(result) else result

    def preflight(self, request: Any) -> Any:
        from agent.runtime_api import RuntimeFailurePhase

        selection = request.selection
        if not (
            selection.provider in PROVIDER_IDS
            and selection.api_mode in API_MODES
            and any(selection.model.startswith(prefix) for prefix in MODEL_PREFIXES)
        ):
            return _failure(
                "claude_runtime_selection_unsupported",
                "Claude runtime selection is outside its declared descriptor",
                RuntimeFailurePhase.PREFLIGHT,
                replay_safe=True,
            )
        _, state_ok = _resume_id(request)
        if not state_ok:
            return _failure(
                "claude_runtime_state_invalid",
                "Claude runtime state is incompatible",
                RuntimeFailurePhase.PREFLIGHT,
                replay_safe=False,
            )
        if not self._check_auth():
            return _failure(
                "claude_subscription_auth_rejected",
                "Claude subscription authentication is unavailable",
                RuntimeFailurePhase.PREFLIGHT,
                replay_safe=False,
            )
        return None

    async def run_turn(self, request: Any, host: Any):
        from agent.runtime_api import (
            RuntimeCancelledEvent,
            RuntimeCompactionEvent,
            RuntimeCompactionPhase,
            RuntimeCompletedEvent,
            RuntimeFailedEvent,
            RuntimeFailurePhase,
            RuntimeStateEnvelope,
            RuntimeStateEvent,
            RuntimeUsageEvent,
            RuntimeUsageReceipt,
        )
        from .content_events import ClaudeSdkEventProjector, ProjectionResult
        from .compaction import SessionCompactionPhase
        from .sdk_session import SessionOutcome

        preflight_failure = self.preflight(request)
        if preflight_failure is not None:
            yield RuntimeFailedEvent(failure=preflight_failure)
            return
        if self._closed:
            yield RuntimeFailedEvent(
                failure=_failure(
                    "claude_runtime_closed",
                    "Claude runtime is closed",
                    RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                    replay_safe=False,
                )
            )
            return
        try:
            cancelled_before_start = host.cancellation_requested()
        except Exception:
            cancelled_before_start = None
        if cancelled_before_start is True:
            yield RuntimeCancelledEvent(reason="cancelled before runtime start")
            return
        if cancelled_before_start is not False:
            yield RuntimeFailedEvent(
                failure=_failure(
                    "claude_runtime_cancellation_unavailable",
                    "Claude runtime cancellation state is unavailable",
                    RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                    replay_safe=False,
                )
            )
            return
        if self._host is not None and host is not self._host:
            yield RuntimeFailedEvent(
                failure=_failure(
                    "claude_runtime_host_binding_changed",
                    "Claude runtime host binding changed",
                    RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                    replay_safe=False,
                )
            )
            return

        try:
            prompt = build_sdk_turn_input(request)
        except TurnInputValidationError as exc:
            yield RuntimeFailedEvent(
                failure=_failure(
                    exc.code,
                    "Claude runtime image input is invalid",
                    RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                    replay_safe=False,
                )
            )
            return
        if prompt is None:
            yield RuntimeFailedEvent(
                failure=_failure(
                    "claude_runtime_prompt_invalid",
                    "Claude runtime requires a bounded text turn",
                    RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                    replay_safe=False,
                )
            )
            return

        if self._session is None and self._sdk is None:
            load_task = asyncio.create_task(self._load_sdk_module())
            cancellation_unavailable_during_load = False
            cancelled_during_load = False
            try:
                while not load_task.done():
                    await asyncio.wait({load_task}, timeout=0.01)
                    if load_task.done():
                        break
                    try:
                        cancellation_state = host.cancellation_requested()
                    except Exception:
                        cancellation_state = None
                    if cancellation_state is True:
                        cancelled_during_load = True
                        break
                    if cancellation_state is not False:
                        cancellation_unavailable_during_load = True
                        break
                if not cancelled_during_load and not cancellation_unavailable_during_load:
                    try:
                        cancellation_state = host.cancellation_requested()
                    except Exception:
                        cancellation_state = None
                    cancelled_during_load = cancellation_state is True
                    cancellation_unavailable_during_load = cancellation_state is not False
                if cancelled_during_load or cancellation_unavailable_during_load:
                    load_task.cancel()
                    await asyncio.gather(load_task, return_exceptions=True)
                    if cancelled_during_load:
                        yield RuntimeCancelledEvent(reason="cancelled during SDK load")
                    else:
                        yield RuntimeFailedEvent(
                            failure=_failure(
                                "claude_runtime_cancellation_unavailable",
                                "Claude runtime cancellation state is unavailable",
                                RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                                replay_safe=False,
                            )
                        )
                    return
                loaded_sdk = await load_task
                if loaded_sdk is None:
                    raise RuntimeError("SDK loader returned no module")
                self._sdk = loaded_sdk
            except asyncio.CancelledError:
                load_task.cancel()
                await asyncio.gather(load_task, return_exceptions=True)
                raise
            except Exception:
                load_task.cancel()
                await asyncio.gather(load_task, return_exceptions=True)
                yield RuntimeFailedEvent(
                    failure=_failure(
                        "claude_runtime_configuration_failed",
                        "Claude runtime configuration failed",
                        RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                        replay_safe=False,
                    )
                )
                return

        resume_id, _ = _resume_id(request)
        queue: asyncio.Queue[ProjectionResult] = asyncio.Queue()
        visible = False
        terminal = False
        bridge: HostToolBridge | None = None
        bridge_execution_start = 0
        try:
            prompt_context = build_sdk_prompt_context(request)
            system_parts = [prompt_context.base_prompt]
            if prompt_context.system_prompt_append:
                system_parts.append(prompt_context.system_prompt_append)
            system_prompt_append = "\n\n".join(
                part for part in system_parts if part
            )[:_MAX_SYSTEM_APPEND] or None
            session_contract = (
                request.selection.provider,
                request.selection.model,
                request.selection.api_mode,
                request.tool_schema_hash,
                system_prompt_append,
            )
            if self._session is None:
                self._host = host
                bridge = HostToolBridge(
                    host, request.tool_schemas, correlation_id=request.correlation_id
                )
                server = bridge.build_sdk_mcp_server(
                    _MCP_SERVER_NAME, sdk_module=self._sdk
                )
                allowed_tools = tuple(
                    f"mcp__{_MCP_SERVER_NAME}__{name}" for name in bridge.tool_names
                )
                configuration = SDKSessionConfiguration.create(
                    cwd=self._cwd,
                    model=request.selection.model,
                    permission_mode="bypassPermissions",
                    system_prompt_append=system_prompt_append,
                    resume_external_session_id=resume_id,
                    parent_env=(
                        self._parent_env if self._parent_env is not None else os.environ
                    ),
                    mcp_servers={_MCP_SERVER_NAME: server},
                    allowed_tools=allowed_tools,
                )
                self._bridge = bridge
                self._session_contract = session_contract
                self._session_configuration = configuration
                self._session = self._new_session(configuration)
            elif session_contract != self._session_contract:
                yield RuntimeFailedEvent(
                    failure=_failure(
                        "claude_runtime_session_contract_changed",
                        "Claude runtime session prompt, tools, or selection changed",
                        RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT,
                        replay_safe=False,
                    )
                )
                return
            elif self._session.can_restart_after_close:
                configuration = self._session_configuration
                assert configuration is not None
                if self._bridge is not None:
                    self._bridge.close()
                replacement_bridge = HostToolBridge(
                    host, request.tool_schemas, correlation_id=request.correlation_id
                )
                replacement_server = replacement_bridge.build_sdk_mcp_server(
                    _MCP_SERVER_NAME, sdk_module=self._sdk
                )
                replacement_allowed_tools = tuple(
                    f"mcp__{_MCP_SERVER_NAME}__{name}"
                    for name in replacement_bridge.tool_names
                )
                replacement_configuration = replace(
                    configuration,
                    resume_external_session_id=resume_id,
                    mcp_servers={_MCP_SERVER_NAME: replacement_server},
                    allowed_tools=replacement_allowed_tools,
                )
                self._bridge = replacement_bridge
                self._session_configuration = replacement_configuration
                self._session = self._new_session(replacement_configuration)
            bridge = self._bridge
            assert bridge is not None
            bridge.begin_turn(request.correlation_id)
            session = self._session
            bridge_execution_start = bridge.host_execution_count
            projector = ClaudeSdkEventProjector(
                runtime_id=RUNTIME_ID,
                provider=request.selection.provider,
                model=request.selection.model,
                billing_mode="subscription_included",
                correlation_id=request.correlation_id,
            )

            async def on_projection(projection: ProjectionResult) -> None:
                await queue.put(projection)

            async def on_compaction_event(event: Any) -> None:
                phases = {
                    SessionCompactionPhase.STARTED: RuntimeCompactionPhase.STARTED,
                    SessionCompactionPhase.COMPLETED: RuntimeCompactionPhase.COMPLETED,
                    SessionCompactionPhase.FAILED: RuntimeCompactionPhase.FAILED,
                    SessionCompactionPhase.WATCHDOG: RuntimeCompactionPhase.WATCHDOG,
                }
                details = (
                    {"watchdog_seconds": event.watchdog_seconds}
                    if event.phase is SessionCompactionPhase.WATCHDOG
                    and event.watchdog_seconds is not None
                    else {}
                )
                await queue.put(
                    ProjectionResult(
                        events=(
                            RuntimeCompactionEvent(
                                phase=phases[event.phase],
                                details=details,
                            ),
                        )
                    )
                )

            task = asyncio.create_task(
                session.run_turn(
                    prompt,
                    projector=projector,
                    on_projection=on_projection,
                    on_compaction_event=on_compaction_event,
                )
            )
            cancel_sent = False
            cancellation_unavailable = False
            cancellation_poll_interval = 0.05
            loop = asyncio.get_running_loop()
            next_cancellation_poll = loop.time() + cancellation_poll_interval
            while not task.done() or not queue.empty():
                now = loop.time()
                if not cancel_sent and now >= next_cancellation_poll:
                    next_cancellation_poll = now + cancellation_poll_interval
                    try:
                        cancellation_state = host.cancellation_requested()
                    except Exception:
                        cancellation_state = None
                    if cancellation_state is True and not cancel_sent:
                        cancel_sent = True
                        await session.cancel()
                    elif cancellation_state is not False and not cancel_sent:
                        cancellation_unavailable = True
                        cancel_sent = True
                        await session.cancel()

                wait_timeout = (
                    0.05
                    if cancel_sent
                    else max(0.001, min(0.05, next_cancellation_poll - loop.time()))
                )
                try:
                    projection = await asyncio.wait_for(queue.get(), wait_timeout)
                except asyncio.TimeoutError:
                    continue
                for event in projection.events:
                    if isinstance(event, RuntimeCompletedEvent):
                        continue
                    if isinstance(event, RuntimeUsageEvent):
                        receipt = event.receipt
                        event = RuntimeUsageEvent(
                            receipt=RuntimeUsageReceipt(
                                runtime_id=RUNTIME_ID,
                                provider=request.selection.provider,
                                model=request.selection.model,
                                billing_mode="subscription_included",
                                cost_status="included",
                                input_tokens=receipt.input_tokens,
                                output_tokens=receipt.output_tokens,
                                cache_read_tokens=receipt.cache_read_tokens,
                                cache_write_tokens=receipt.cache_write_tokens,
                                reasoning_tokens=receipt.reasoning_tokens,
                                replay_safe=False,
                                correlation_id=request.correlation_id,
                            )
                        )
                    else:
                        kind = getattr(event, "kind", None)
                        visible = visible or getattr(kind, "value", None) == "content"
                    yield event

            result = await task
            if cancellation_unavailable:
                phase = (
                    RuntimeFailurePhase.AFTER_SIDE_EFFECTS
                    if bridge.host_execution_count > bridge_execution_start
                    else RuntimeFailurePhase.AFTER_VISIBLE_OUTPUT
                    if visible
                    else RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT
                )
                terminal = True
                bridge.close()
                yield RuntimeFailedEvent(
                    failure=_failure(
                        "claude_runtime_cancellation_unavailable",
                        "Claude runtime cancellation state is unavailable",
                        phase,
                        replay_safe=False,
                    )
                )
                await session.release_background_results()
                return
            if result.outcome is SessionOutcome.COMPLETE:
                final_text = result.final_text or ""
                if result.state_update.external_session_id is not None:
                    yield RuntimeStateEvent(
                        state=RuntimeStateEnvelope(
                            runtime_id=RUNTIME_ID,
                            schema_version=1,
                            state={
                                "external_session_id": result.state_update.external_session_id
                            },
                        )
                    )
                terminal = True
                # ``text`` is the provider-neutral completion payload.  The
                # remaining fields are the current Hermes v1 conversation
                # adapter shape; retain them until the host normalizes generic
                # completion/content events itself.
                response_messages = [dict(message) for message in request.messages]
                response_messages.append({"role": "assistant", "content": final_text})
                yield RuntimeCompletedEvent(
                    result={
                        "text": final_text,
                        "final_response": final_text,
                        "messages": response_messages,
                        "completed": True,
                        "partial": False,
                        "error": None,
                        "api_calls": 1,
                        "provider": request.selection.provider,
                        "model": request.selection.model,
                    }
                )
                await session.release_background_results()
                return
            if result.outcome is SessionOutcome.CANCELLED:
                terminal = True
                bridge.close()
                yield RuntimeCancelledEvent(reason="cancelled")
                await session.release_background_results()
                return

            phase = (
                RuntimeFailurePhase.AFTER_SIDE_EFFECTS
                if bridge.host_execution_count > bridge_execution_start
                else RuntimeFailurePhase.AFTER_VISIBLE_OUTPUT
                if visible
                else RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT
            )
            code = (
                "claude_subscription_billing_blocked"
                if result.outcome is SessionOutcome.BILLING_BLOCKED
                else result.error_code or "claude_runtime_failed"
            )
            terminal = True
            if session.can_restart_after_close:
                bridge.close()
            yield RuntimeFailedEvent(
                failure=_failure(
                    code,
                    "Claude runtime turn failed",
                    phase,
                    replay_safe=False,
                )
            )
            await session.release_background_results()
        except asyncio.CancelledError:
            if self._session is not None:
                await self._session.cancel()
            if self._bridge is not None:
                self._bridge.close()
            raise
        except Exception:
            if self._session is not None:
                await self._session.cancel()
            if self._bridge is not None:
                self._bridge.close()
            if not terminal:
                side_effects = (
                    bridge.host_execution_count - bridge_execution_start
                    if bridge is not None
                    else 0
                )
                phase = (
                    RuntimeFailurePhase.AFTER_SIDE_EFFECTS
                    if side_effects
                    else RuntimeFailurePhase.AFTER_VISIBLE_OUTPUT
                    if visible
                    else RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT
                )
                yield RuntimeFailedEvent(
                    failure=_failure(
                        (
                            "claude_runtime_configuration_failed"
                            if self._session is None
                            else "claude_runtime_failed"
                        ),
                        (
                            "Claude runtime configuration failed"
                            if self._session is None
                            else "Claude runtime turn failed"
                        ),
                        phase,
                        replay_safe=False,
                    )
                )
                if self._session is not None:
                    await self._session.release_background_results()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._bridge is not None:
            self._bridge.close()
        if self._session is not None:
            await self._session.close()


def create_runtime() -> ClaudeAgentSDKRuntime:
    """Return a lazy runtime without importing the optional SDK."""

    return ClaudeAgentSDKRuntime()


runtime_factory = create_runtime

__all__ = ["ClaudeAgentSDKRuntime", "create_runtime", "runtime_factory"]

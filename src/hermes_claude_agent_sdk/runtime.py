"""AgentRuntime v1 composition over the public Claude Agent SDK adapter."""

from __future__ import annotations

import asyncio
import importlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from .compatibility import (
    API_MODES,
    PROVIDER_IDS,
    RUNTIME_ID,
    check_model_compatibility,
    is_supported_model_id,
)
from .configuration import SDKSessionConfiguration
from .tool_bridge import HostToolBridge
from .turn_input import TurnInputValidationError, build_sdk_turn_input

_MCP_SERVER_NAME = "hermes-tools"
_MAX_TURN_TOOL_OBSERVATIONS = 64
_MAX_TOOL_OBSERVATION_NAME_CHARS = 128
_SAFE_TOOL_OBSERVATION_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _safe_tool_observation_name(value: Any) -> str | None:
    """Retain only a bounded, identifier-shaped SDK tool name."""

    if type(value) is not str:
        return None
    value = value.strip()
    if (
        not value
        or len(value) > _MAX_TOOL_OBSERVATION_NAME_CHARS
        or _SAFE_TOOL_OBSERVATION_NAME.fullmatch(value) is None
    ):
        return None
    return value


def _failure(
    code: str,
    message: str,
    phase: Any,
    *,
    replay_safe: bool,
    retryable: bool = False,
) -> Any:
    from agent.runtime_api import RuntimeFailure

    return RuntimeFailure(
        code=code,
        message=message,
        phase=phase,
        replay_safe=replay_safe,
        retryable=retryable,
    )


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
        client_factory: Callable[..., Any] | None = None,
        cwd: str = ".",
        parent_env: Mapping[str, object] | None = None,
        compaction_watchdog_seconds: float = 600.0,
    ) -> None:
        self._auth_probe = auth_probe
        self._sdk = sdk_module
        self._client_factory = client_factory
        self._cwd = cwd
        self._parent_env = parent_env
        self._compaction_watchdog_seconds = compaction_watchdog_seconds
        self._session: Any | None = None
        self._bridge: HostToolBridge | None = None
        self._host: Any | None = None
        self._session_contract: tuple[str, str, str, str, str | None] | None = None
        self._session_configuration: SDKSessionConfiguration | None = None
        self._projector: Any | None = None
        self._last_turn_tool_observations: tuple[str, ...] = ()
        # One successful preflight may be consumed only by the exact request
        # object that was checked.  This avoids probing auth twice on the
        # supported host path without caching authorization across turns.
        self._preflight_request: Any | None = None
        self._closed = False

    @property
    def last_turn_tool_observations(self) -> tuple[str, ...]:
        """Return bounded SDK tool-name observations from the last turn only."""

        return self._last_turn_tool_observations

    def _default_auth_probe(self) -> Any:
        module = importlib.import_module(".auth", __package__)
        return module.probe_claude_auth()

    def _check_auth(self) -> bool:
        try:
            result = (self._auth_probe or self._default_auth_probe)()
            category = getattr(result, "category", None)
            category = getattr(category, "value", category)
            return (
                getattr(result, "allowed", None) is True
                and category == "subscription_oauth"
            )
        except Exception:
            return False

    def _new_session(self, configuration: SDKSessionConfiguration) -> Any:
        from .sdk_session import SDKSession

        return SDKSession(
            configuration,
            sdk_module=self._sdk,
            client_factory=self._client_factory,
            compaction_watchdog_seconds=self._compaction_watchdog_seconds,
        )

    def preflight(self, request: Any) -> Any:
        from agent.runtime_api import RuntimeFailurePhase

        self._preflight_request = None
        selection = request.selection
        if not (
            selection.provider in PROVIDER_IDS
            and selection.api_mode in API_MODES
            and is_supported_model_id(selection.model)
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
        try:
            model_compatibility = check_model_compatibility(selection.model)
            model_compatible = (
                isinstance(model_compatibility, Mapping)
                and model_compatibility.get("compatible") is True
            )
        except Exception:
            model_compatible = False
        if not model_compatible:
            return _failure(
                "claude_runtime_sdk_compatibility_unsupported",
                (
                    "Claude runtime SDK/CLI compatibility is unavailable "
                    "for the selected model"
                ),
                RuntimeFailurePhase.PREFLIGHT,
                replay_safe=True,
            )
        if not self._check_auth():
            return _failure(
                "claude_subscription_auth_rejected",
                "Claude subscription authentication is unavailable",
                RuntimeFailurePhase.PREFLIGHT,
                replay_safe=False,
            )
        self._preflight_request = request
        return None

    def _consume_preflight(self, request: Any) -> bool:
        preflight_request = self._preflight_request
        self._preflight_request = None
        return preflight_request is request

    async def run_turn(self, request: Any, host: Any):
        self._last_turn_tool_observations = ()
        from agent.runtime_api import (
            RuntimeCancelledEvent,
            RuntimeCompactionEvent,
            RuntimeCompactionPhase,
            RuntimeCompletedEvent,
            RuntimeFailedEvent,
            RuntimeFailurePhase,
            RuntimeStateEnvelope,
            RuntimeStateEvent,
            RuntimeToolRequestEvent,
            RuntimeUsageEvent,
        )
        from .content_events import ClaudeSdkEventProjector, ProjectionResult
        from .compaction import SessionCompactionPhase
        from .sdk_session import SessionOutcome

        if not self._consume_preflight(request):
            preflight_failure = self.preflight(request)
            if preflight_failure is not None:
                yield RuntimeFailedEvent(failure=preflight_failure)
                return
            if not self._consume_preflight(request):
                yield RuntimeFailedEvent(
                    failure=_failure(
                        "claude_runtime_preflight_unavailable",
                        "Claude runtime preflight could not be consumed",
                        RuntimeFailurePhase.PREFLIGHT,
                        replay_safe=False,
                    )
                )
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

        resume_id, _ = _resume_id(request)
        queue: asyncio.Queue[ProjectionResult] = asyncio.Queue()
        visible = False
        terminal = False
        bridge: HostToolBridge | None = None
        bridge_execution_start = 0
        try:
            # The host has already composed and snapshotted the prompt.  The
            # SDK receives that exact value as its public system prompt; it
            # must not add a preset or plugin-owned context of its own.
            prompt_snapshot = request.prompt_snapshot
            session_contract = (
                request.selection.provider,
                request.selection.model,
                request.selection.api_mode,
                request.tool_schema_hash,
                prompt_snapshot,
            )

            def new_projector() -> Any:
                return ClaudeSdkEventProjector(
                    runtime_id=RUNTIME_ID,
                    provider=request.selection.provider,
                    # The request model is selection metadata only.  The
                    # projector uses it to classify exact/mismatch; effective
                    # identity still comes only from SDK message/model_usage
                    # evidence and never falls back to this value.
                    model=request.selection.model,
                    billing_mode="subscription_included",
                    correlation_id=request.correlation_id,
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
                    prompt_snapshot=prompt_snapshot,
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
                self._projector = new_projector()
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
            elif self._session.can_restart_after_cancel:
                configuration = self._session_configuration
                assert configuration is not None
                replacement_configuration = replace(
                    configuration,
                    resume_external_session_id=resume_id,
                )
                self._session_configuration = replacement_configuration
                self._session = self._new_session(replacement_configuration)
                # A cancelled SDK client is a new runtime session.  Its
                # model-provenance evidence must start empty even when it
                # resumes the same external conversation state.
                self._projector = new_projector()
            bridge = self._bridge
            assert bridge is not None
            bridge.begin_turn(request.correlation_id)
            session = self._session
            projector = self._projector
            assert projector is not None
            projector.begin_turn(correlation_id=request.correlation_id)
            bridge_execution_start = bridge.host_execution_count
            # The SDK must report only the exact fully-qualified names from
            # this one strict Hermes MCP server. Raw aliases are deliberately
            # not accepted because they can collide with Claude-native tools.
            allowed_tool_names = frozenset(
                f"mcp__{_MCP_SERVER_NAME}__{name}" for name in bridge.tool_names
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
            terminal_model_provenance = {
                "effective_model": "unknown",
                "canonical_model": "unknown",
                "model_resolution": "unknown",
            }
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
                if projection.is_result:
                    if projection.model_resolution == "ambiguous":
                        terminal_model_provenance = {
                            "effective_model": "unknown",
                            "canonical_model": "unknown",
                            "model_resolution": "ambiguous",
                        }
                    else:
                        terminal_model_provenance = {
                            "effective_model": projection.effective_model or "unknown",
                            "canonical_model": projection.canonical_model or "unknown",
                            "model_resolution": projection.model_resolution,
                        }
                unexpected_native_tool = next(
                    (
                        event
                        for event in projection.events
                        if isinstance(event, RuntimeToolRequestEvent)
                        and _safe_tool_observation_name(getattr(event, "name", None))
                        not in allowed_tool_names
                    ),
                    None,
                )
                if unexpected_native_tool is not None:
                    phase = (
                        RuntimeFailurePhase.AFTER_SIDE_EFFECTS
                        if bridge.host_execution_count > bridge_execution_start
                        else RuntimeFailurePhase.AFTER_VISIBLE_OUTPUT
                        if visible
                        else RuntimeFailurePhase.BEFORE_VISIBLE_OUTPUT
                    )
                    await session.cancel()
                    await task
                    terminal = True
                    yield RuntimeFailedEvent(
                        failure=_failure(
                            "claude_runtime_native_tool_unsupported",
                            "Claude runtime emitted an unsupported native tool event",
                            phase,
                            replay_safe=False,
                        )
                    )
                    return

                for event in projection.events:
                    if isinstance(event, RuntimeCompletedEvent):
                        continue
                    if isinstance(event, RuntimeToolRequestEvent):
                        observed_name = _safe_tool_observation_name(
                            getattr(event, "name", None)
                        )
                        if (
                            observed_name is not None
                            and len(self._last_turn_tool_observations)
                            < _MAX_TURN_TOOL_OBSERVATIONS
                        ):
                            self._last_turn_tool_observations = (
                                *self._last_turn_tool_observations,
                                observed_name,
                            )
                        # SDK MCP handlers already execute the host-owned tool
                        # and return its bounded result to the SDK.  The v1
                        # host dispatcher also treats a surfaced
                        # RuntimeToolRequestEvent as an execution request;
                        # forwarding this projection would therefore repeat
                        # the same side effect without a public result channel
                        # back into the SDK.  Keep tool requests internal to
                        # this MCP-backed runtime so the bridge is the one
                        # execution/result path.
                        continue
                    if isinstance(event, RuntimeUsageEvent):
                        receipt = event.receipt
                        event = RuntimeUsageEvent(
                            receipt=replace(
                                receipt,
                                runtime_id=RUNTIME_ID,
                                provider=request.selection.provider,
                                billing_mode="subscription_included",
                                cost_status="included",
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
                yield RuntimeFailedEvent(
                    failure=_failure(
                        "claude_runtime_cancellation_unavailable",
                        "Claude runtime cancellation state is unavailable",
                        phase,
                        replay_safe=False,
                    )
                )
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
                        # Keep the legacy model key as the safe effective /
                        # canonical identity, never as selected request data.
                        "model": (
                            terminal_model_provenance["canonical_model"]
                            if terminal_model_provenance["canonical_model"] != "unknown"
                            else terminal_model_provenance["effective_model"]
                        ),
                        "selected_model": request.selection.model,
                        **terminal_model_provenance,
                    }
                )
                return
            if result.outcome is SessionOutcome.CANCELLED:
                terminal = True
                yield RuntimeCancelledEvent(reason="cancelled")
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
                else "claude_runtime_native_tool_unsupported"
                if result.error_code == "sdk_native_tool_unsupported"
                else result.error_code or "claude_runtime_failed"
            )
            terminal = True
            yield RuntimeFailedEvent(
                failure=_failure(
                    code,
                    "Claude runtime turn failed",
                    phase,
                    replay_safe=False,
                    retryable=result.retryable,
                )
            )
            return
        except asyncio.CancelledError:
            if self._session is not None:
                await self._session.cancel()
            raise
        except Exception:
            if self._session is not None:
                await self._session.cancel()
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

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._preflight_request = None
        if self._session is not None:
            await self._session.close()


def create_runtime() -> ClaudeAgentSDKRuntime:
    """Return a lazy runtime without importing the optional SDK."""

    return ClaudeAgentSDKRuntime()


runtime_factory = create_runtime

__all__ = ["ClaudeAgentSDKRuntime", "create_runtime", "runtime_factory"]

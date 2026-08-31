"""AgentRuntime v1 composition over the public Claude Agent SDK adapter."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .compatibility import API_MODES, MODEL_PREFIXES, PROVIDER_IDS, RUNTIME_ID
from .configuration import SDKSessionConfiguration
from .prompt_context import build_sdk_prompt_context
from .tool_bridge import HostToolBridge

_MAX_TURN_TEXT = 32_000
_MAX_SYSTEM_APPEND = 32_000
_MCP_SERVER_NAME = "hermes-tools"


def _failure(code: str, message: str, phase: Any, *, replay_safe: bool) -> Any:
    from agent.runtime_api import RuntimeFailure

    return RuntimeFailure(code=code, message=message, phase=phase, replay_safe=replay_safe)


def _bounded_turn_text(request: Any) -> str | None:
    """Extract the last bounded public user-text turn without coercing objects."""

    messages = getattr(request, "messages", ())
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return None
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text[:_MAX_TURN_TEXT] if text else None
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            parts: list[str] = []
            used = 0
            for block in content:
                if not isinstance(block, Mapping) or block.get("type") != "text":
                    continue
                value = block.get("text")
                if not isinstance(value, str):
                    continue
                value = value.strip()
                if not value:
                    continue
                remaining = _MAX_TURN_TEXT - used
                if remaining <= 0:
                    break
                parts.append(value[:remaining])
                used += min(len(value), remaining)
            text = "\n".join(parts).strip()
            return text[:_MAX_TURN_TEXT] if text else None
        return None
    return None


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
    ) -> None:
        self._auth_probe = auth_probe
        self._sdk = sdk_module
        self._client_factory = client_factory
        self._cwd = cwd
        self._parent_env = parent_env
        self._auth_allowed: bool | None = None
        self._session: Any | None = None
        self._closed = False

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
            RuntimeCompletedEvent,
            RuntimeFailedEvent,
            RuntimeFailurePhase,
            RuntimeStateEnvelope,
            RuntimeStateEvent,
            RuntimeUsageEvent,
            RuntimeUsageReceipt,
        )
        from .content_events import ClaudeSdkEventProjector, ProjectionResult
        from .sdk_session import SDKSession, SessionOutcome

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

        prompt = _bounded_turn_text(request)
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
        try:
            prompt_context = build_sdk_prompt_context(request)
            system_parts = [prompt_context.base_prompt]
            if prompt_context.system_prompt_append:
                system_parts.append(prompt_context.system_prompt_append)
            system_prompt_append = "\n\n".join(
                part for part in system_parts if part
            )[:_MAX_SYSTEM_APPEND] or None
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
                parent_env=self._parent_env if self._parent_env is not None else os.environ,
                mcp_servers={_MCP_SERVER_NAME: server},
                allowed_tools=allowed_tools,
            )
            projector = ClaudeSdkEventProjector(
                runtime_id=RUNTIME_ID,
                provider=request.selection.provider,
                model=request.selection.model,
                billing_mode="subscription_included",
                correlation_id=request.correlation_id,
            )

            async def on_projection(projection: ProjectionResult) -> None:
                await queue.put(projection)

            session = SDKSession(
                configuration,
                sdk_module=self._sdk,
                client_factory=self._client_factory,
                projector=projector,
                on_projection=on_projection,
            )
            self._session = session
            task = asyncio.create_task(session.run_turn(prompt))
            cancel_sent = False
            while not task.done() or not queue.empty():
                try:
                    projection = await asyncio.wait_for(queue.get(), 0.05)
                except asyncio.TimeoutError:
                    if host.cancellation_requested() and not cancel_sent:
                        cancel_sent = True
                        await session.cancel()
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
            if result.outcome is SessionOutcome.COMPLETE:
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
                yield RuntimeCompletedEvent(result={"text": result.final_text or ""})
                return
            if result.outcome is SessionOutcome.CANCELLED:
                terminal = True
                yield RuntimeCancelledEvent(reason="cancelled")
                return

            phase = (
                RuntimeFailurePhase.AFTER_SIDE_EFFECTS
                if bridge.host_execution_count
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
            yield RuntimeFailedEvent(
                failure=_failure(
                    code,
                    "Claude runtime turn failed",
                    phase,
                    replay_safe=False,
                )
            )
        except asyncio.CancelledError:
            if self._session is not None:
                await self._session.cancel()
            raise
        except Exception:
            if self._session is not None:
                await self._session.cancel()
            if not terminal:
                side_effects = bridge.host_execution_count if bridge is not None else 0
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
        if self._session is not None:
            await self._session.close()


def create_runtime() -> ClaudeAgentSDKRuntime:
    """Return a lazy runtime without importing the optional SDK."""

    return ClaudeAgentSDKRuntime()


runtime_factory = create_runtime

__all__ = ["ClaudeAgentSDKRuntime", "create_runtime", "runtime_factory"]

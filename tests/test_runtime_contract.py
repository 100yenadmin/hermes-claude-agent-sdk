"""Fake-SDK contract tests against the public AgentRuntime v1 host API."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from types import ModuleType

import pytest

runtime_api = pytest.importorskip("agent.runtime_api")

from agent.runtime_dispatch import build_runtime_turn_request  # noqa: E402
import hermes_claude_agent_sdk as plugin  # noqa: E402
import hermes_claude_agent_sdk.runtime as runtime_module  # noqa: E402


class _Context:
    def __init__(self) -> None:
        self.registration = None

    def register_agent_runtime(self, *, descriptor, factory):
        runtime_api.validate_runtime_descriptor(descriptor)
        self.registration = (descriptor, factory)


class _Host:
    def cancellation_requested(self) -> bool:
        return False


def _request():
    return build_runtime_turn_request(
        provider="claude-agent-sdk",
        model="claude-fable-5",
        api_mode="agent_runtime",
        messages=({"role": "user", "content": "hello"},),
        prompt_snapshot="stable prompt",
        tool_schemas=(),
        correlation_id="synthetic-correlation",
    )


def test_register_uses_public_descriptor_and_retains_zero_argument_factory(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    context = _Context()

    plugin.register(context)

    descriptor, factory = context.registration
    assert descriptor.runtime_id == plugin.RUNTIME_ID
    assert descriptor.provider_ids == frozenset({"claude-agent-sdk"})
    assert descriptor.api_modes == frozenset({"agent_runtime"})
    assert "claude-fable-5".startswith(descriptor.model_prefixes[0])
    assert factory is plugin.create_runtime
    assert "claude_agent_sdk" not in sys.modules


def test_factory_and_preflight_reject_incompatible_selection_before_sdk_import(
    monkeypatch,
):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    imports = []

    def forbidden_import(name):
        imports.append(name)
        raise AssertionError("incompatible preflight must not import the SDK")

    monkeypatch.setattr(runtime_module.importlib, "import_module", forbidden_import)
    runtime = plugin.create_runtime()
    request = build_runtime_turn_request(
        provider="claude-agent-sdk",
        model="claude-fable-5",
        api_mode="anthropic_messages",
        messages=(),
        prompt_snapshot="stable prompt",
        tool_schemas=(),
    )

    failure = runtime.preflight(request)

    assert failure is not None
    assert failure.code == "claude_runtime_selection_unsupported"
    assert imports == []
    assert "claude_agent_sdk" not in sys.modules

    async def collect():
        return [event async for event in runtime.run_turn(request, _Host())]

    events = asyncio.run(collect())
    assert [event.kind.value for event in events] == ["failed"]
    assert events[0].failure.phase.value == "preflight"
    assert imports == []
    assert "claude_agent_sdk" not in sys.modules


def test_public_host_rejects_incompatible_descriptor_before_factory_or_sdk(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    context = _Context()
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return object()

    descriptor = plugin.build_runtime_descriptor()
    incompatible = replace(
        descriptor,
        runtime_api_min=runtime_api.RUNTIME_API_VERSION + 1,
        runtime_api_max=runtime_api.RUNTIME_API_VERSION + 1,
    )

    with pytest.raises(runtime_api.RuntimeCompatibilityError, match="runtime API"):
        context.register_agent_runtime(
            descriptor=incompatible,
            factory=factory,
        )

    assert factory_calls == 0
    assert context.registration is None
    assert "claude_agent_sdk" not in sys.modules


def test_doctor_reports_host_compatibility_without_importing_sdk(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)

    report = plugin.doctor()

    assert report["status"] == "compatible"
    assert report["compatible"] is True
    assert report["runtime_api"]["host"] == runtime_api.RUNTIME_API_VERSION
    assert report["capabilities"]["missing"] == []
    assert "claude_agent_sdk" not in sys.modules


def test_factory_converts_only_explicit_fake_events_and_never_queries_sdk(monkeypatch):
    sdk = ModuleType("claude_agent_sdk")

    def query(*args, **kwargs):
        raise AssertionError("the thin contract shell must not call query")

    sdk.query = query
    sdk.iter_events = lambda request: [
        {"type": "status", "message": "fake-start"},
        {"type": "content", "text": "hello"},
        {
            "type": "usage",
            "input_tokens": 2,
            "output_tokens": 3,
            "billing_mode": "synthetic",
            "cost_status": "not_recorded",
            "replay_safe": True,
        },
        {"type": "completed", "result": {"text": "hello"}},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)

    runtime = plugin.create_runtime()
    request = _request()

    async def collect():
        return [event async for event in runtime.run_turn(request, _Host())]

    events = asyncio.run(collect())

    assert [event.kind.value for event in events] == [
        "status",
        "content",
        "usage",
        "completed",
    ]
    assert events[1].text == "hello"
    assert events[2].receipt.input_tokens == 2
    assert events[-1].result == {"text": "hello"}
    asyncio.run(runtime.close())


def test_incompatible_host_manifest_is_reported_before_any_sdk_access(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)

    report = plugin.doctor(
        {
            "runtime_api_version": runtime_api.RUNTIME_API_VERSION + 1,
            "host_capabilities": ["cancellation_v1"],
        }
    )

    assert report["status"] == "incompatible"
    assert report["runtime_api"]["compatible"] is False
    assert "host_approval_v1" in report["capabilities"]["missing"]
    assert "claude_agent_sdk" not in sys.modules

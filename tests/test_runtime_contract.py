"""Fake-SDK contract tests against the public AgentRuntime v1 host API."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace

import pytest

runtime_api = pytest.importorskip("agent.runtime_api")

from agent.runtime_dispatch import build_runtime_turn_request  # noqa: E402
import hermes_claude_agent_sdk as plugin  # noqa: E402
import hermes_claude_agent_sdk.runtime as runtime_module  # noqa: E402


class _Context:
    def __init__(self) -> None:
        self.registration = None
        self.provider_profile = None

    def register_provider_profile(self, profile):
        self.provider_profile = profile

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
    assert "background_delivery_v1" in descriptor.required_host_capabilities
    assert "provider_profile_registration_v1" in descriptor.required_host_capabilities
    assert context.provider_profile.name == "claude-agent-sdk"
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


def test_auth_rejection_stops_before_sdk_import_or_query(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    imports = []

    def record_import(name, *args, **kwargs):
        imports.append(name)
        raise AssertionError("rejected auth must stop before SDK import")

    class AuthResult:
        allowed = False

    monkeypatch.setattr(runtime_module.importlib, "import_module", record_import)
    runtime = runtime_module.ClaudeAgentSDKRuntime(auth_probe=lambda: AuthResult())
    request = _request()

    async def collect():
        return [event async for event in runtime.run_turn(request, _Host())]

    events = asyncio.run(collect())

    assert [event.kind.value for event in events] == ["failed"]
    assert events[0].failure.phase.value == "preflight"
    assert events[0].failure.code == "claude_subscription_auth_rejected"
    assert imports == []
    assert "claude_agent_sdk" not in sys.modules


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
    assert "background_delivery_v1" in report["capabilities"]["missing"]
    assert "host_approval_v1" in report["capabilities"]["missing"]
    assert "claude_agent_sdk" not in sys.modules

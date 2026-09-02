"""Fake-SDK contract tests against the public AgentRuntime v1 host API."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace

import pytest

runtime_api = pytest.importorskip("agent.runtime_api")

from agent.runtime_dispatch import build_runtime_turn_request  # noqa: E402
import hermes_claude_agent_sdk as plugin  # noqa: E402
import hermes_claude_agent_sdk.compatibility as compatibility_module  # noqa: E402
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
    assert "runtime_model_provenance_v1" in descriptor.required_host_capabilities
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


def test_fable_5_1_stale_sdk_is_rejected_before_auth_or_sdk(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    imports = []
    auth_calls = []

    def forbidden_import(name, *args, **kwargs):
        imports.append(name)
        raise AssertionError("stale Fable 5.1 preflight must not import the SDK")

    class AuthResult:
        allowed = True
        category = "subscription_oauth"

    monkeypatch.setattr(
        compatibility_module,
        "_sdk_metadata",
        lambda: {
            "distribution": "claude-agent-sdk",
            "installed_version": "0.2.144",
            "bundled_cli_version": "2.1.239",
            "metadata_status": "compatible",
        },
    )
    monkeypatch.setattr(runtime_module.importlib, "import_module", forbidden_import)
    runtime = runtime_module.ClaudeAgentSDKRuntime(
        auth_probe=lambda: (auth_calls.append(True), AuthResult())[1]
    )
    request = _request()
    request = replace(
        request,
        selection=replace(request.selection, model="claude-fable-5-1"),
    )

    failure = runtime.preflight(request)
    assert failure is not None
    assert failure.code == "claude_runtime_sdk_compatibility_unsupported"
    assert auth_calls == []
    assert imports == []
    assert "claude_agent_sdk" not in sys.modules

    async def collect():
        return [event async for event in runtime.run_turn(request, _Host())]

    events = asyncio.run(collect())
    assert [event.kind.value for event in events] == ["failed"]
    assert events[0].failure.code == "claude_runtime_sdk_compatibility_unsupported"
    assert auth_calls == []
    assert imports == []
    assert "claude_agent_sdk" not in sys.modules


def test_fable_5_1_exact_successor_is_accepted_without_sdk_import(monkeypatch):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    imports = []
    auth_calls = []

    def forbidden_import(name, *args, **kwargs):
        imports.append(name)
        raise AssertionError("preflight must not import the SDK")

    class AuthResult:
        allowed = True
        category = "subscription_oauth"

    monkeypatch.setattr(
        compatibility_module,
        "_sdk_metadata",
        lambda: {
            "distribution": "claude-agent-sdk",
            "installed_version": "0.2.151",
            "bundled_cli_version": "2.1.258",
            "metadata_status": "compatible",
        },
    )
    monkeypatch.setattr(runtime_module.importlib, "import_module", forbidden_import)
    runtime = runtime_module.ClaudeAgentSDKRuntime(
        auth_probe=lambda: (auth_calls.append(True), AuthResult())[1]
    )
    request = _request()
    request = replace(
        request, selection=replace(request.selection, model="claude-fable-5-1")
    )

    assert runtime.preflight(request) is None
    assert auth_calls == [True]
    assert imports == []
    assert "claude_agent_sdk" not in sys.modules


def test_fable_5_stays_eligible_on_frozen_sdk(monkeypatch):
    monkeypatch.setattr(
        compatibility_module,
        "_sdk_metadata",
        lambda: {
            "distribution": "claude-agent-sdk",
            "installed_version": "0.2.144",
            "bundled_cli_version": "2.1.239",
            "metadata_status": "compatible",
        },
    )
    runtime = runtime_module.ClaudeAgentSDKRuntime(
        auth_probe=lambda: type(
            "AuthResult", (), {"allowed": True, "category": "subscription_oauth"}
        )()
    )

    assert runtime.preflight(_request()) is None


@pytest.mark.parametrize(
    ("installed_version", "bundled_cli_version"),
    (
        (None, "2.1.258"),
        ("malformed", "2.1.258"),
        ("0.2.151", None),
        ("0.2.151", "malformed"),
        ("0.2.151", "2.1.256"),
        ("0.2.152", "2.1.258"),
    ),
)
def test_fable_5_1_rejects_missing_or_malformed_metadata_before_auth(
    monkeypatch, installed_version, bundled_cli_version
):
    auth_calls = []
    monkeypatch.setattr(
        compatibility_module,
        "_sdk_metadata",
        lambda: {
            "distribution": "claude-agent-sdk",
            "installed_version": installed_version,
            "bundled_cli_version": bundled_cli_version,
            "metadata_status": "compatible",
        },
    )
    runtime = runtime_module.ClaudeAgentSDKRuntime(
        auth_probe=lambda: auth_calls.append(True)
    )

    request = _request()
    request = replace(
        request, selection=replace(request.selection, model="claude-fable-5-1")
    )
    failure = runtime.preflight(request)

    assert failure is not None
    assert failure.code == "claude_runtime_sdk_compatibility_unsupported"
    assert auth_calls == []


def test_doctor_exposes_bounded_sdk_cli_metadata_without_paths_or_secrets(monkeypatch):
    monkeypatch.setattr(
        compatibility_module,
        "_sdk_metadata",
        lambda: {
            "distribution": "claude-agent-sdk",
            "installed_version": "0.2.151",
            "bundled_cli_version": "2.1.258",
            "metadata_status": "compatible",
        },
    )

    report = plugin.doctor()

    assert report["sdk"]["installed_version"] == "0.2.151"
    assert report["sdk"]["bundled_cli_version"] == "2.1.258"
    assert report["sdk"]["fable_5_1"]["compatible"] is True
    rendered = plugin.doctor_json()
    assert "/Users/" not in rendered
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in rendered


@pytest.mark.parametrize(
    "model",
    (
        "fable",
        "anthropic/claude-fable-5.1",
        "claude-fable 5.1",
        "Claude-fable-5-1",
        "claude-",
    ),
)
def test_non_direct_fable_ids_are_rejected_before_auth_or_sdk(monkeypatch, model):
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    auth_calls = []
    imports = []

    def forbidden_import(name, *args, **kwargs):
        imports.append(name)
        raise AssertionError("incompatible preflight must not import the SDK")

    def auth_probe():
        auth_calls.append(True)
        raise AssertionError("incompatible selection must not inspect auth")

    monkeypatch.setattr(runtime_module.importlib, "import_module", forbidden_import)
    runtime = runtime_module.ClaudeAgentSDKRuntime(auth_probe=auth_probe)
    request = _request()
    request = replace(request, selection=replace(request.selection, model=model))

    failure = runtime.preflight(request)

    assert failure is not None
    assert failure.code == "claude_runtime_selection_unsupported"
    assert auth_calls == []
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


def test_preflight_revalidates_subscription_auth_for_each_turn() -> None:
    decisions = iter((True, False))
    auth_calls: list[bool] = []

    def auth_probe():
        allowed = next(decisions)
        auth_calls.append(allowed)
        return type(
            "AuthResult",
            (),
            {"allowed": allowed, "category": "subscription_oauth"},
        )()

    runtime = runtime_module.ClaudeAgentSDKRuntime(auth_probe=auth_probe)
    first = _request()
    second = replace(first, correlation_id="synthetic-correlation-2")

    assert runtime.preflight(first) is None
    failure = runtime.preflight(second)

    assert failure is not None
    assert failure.code == "claude_subscription_auth_rejected"
    assert auth_calls == [True, False]


def test_preflight_rechecks_after_transient_auth_probe_failure() -> None:
    auth_calls = 0

    def auth_probe():
        nonlocal auth_calls
        auth_calls += 1
        if auth_calls == 1:
            raise RuntimeError("synthetic auth probe failure")
        return type(
            "AuthResult",
            (),
            {"allowed": True, "category": "subscription_oauth"},
        )()

    runtime = runtime_module.ClaudeAgentSDKRuntime(auth_probe=auth_probe)
    first = _request()
    second = replace(first, correlation_id="synthetic-correlation-2")

    failure = runtime.preflight(first)
    assert failure is not None
    assert failure.code == "claude_subscription_auth_rejected"
    assert runtime.preflight(second) is None
    assert auth_calls == 2


def test_host_preflight_is_consumed_once_and_next_turn_revalidates() -> None:
    auth_calls = 0

    def auth_probe():
        nonlocal auth_calls
        auth_calls += 1
        return type(
            "AuthResult",
            (),
            {"allowed": True, "category": "subscription_oauth"},
        )()

    class CancelledHost(_Host):
        def cancellation_requested(self):
            return True

    runtime = runtime_module.ClaudeAgentSDKRuntime(auth_probe=auth_probe)
    first = _request()
    second = replace(first, correlation_id="synthetic-correlation-2")

    async def collect(request):
        return [event async for event in runtime.run_turn(request, CancelledHost())]

    assert runtime.preflight(first) is None
    first_events = asyncio.run(collect(first))
    assert [event.kind.value for event in first_events] == ["cancelled"]
    assert auth_calls == 1

    assert runtime.preflight(second) is None
    second_events = asyncio.run(collect(second))
    assert [event.kind.value for event in second_events] == ["cancelled"]
    assert auth_calls == 2


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
    assert "runtime_model_provenance_v1" in report["capabilities"]["missing"]
    assert "claude_agent_sdk" not in sys.modules

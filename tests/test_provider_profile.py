from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
_HOST_ROOT_VALUE = os.environ.get("HERMES_AGENT_HOST_ROOT")
HOST_ROOT = Path(_HOST_ROOT_VALUE) if _HOST_ROOT_VALUE else None
requires_host_root = pytest.mark.skipif(
    HOST_ROOT is None or not HOST_ROOT.is_dir(),
    reason="HERMES_AGENT_HOST_ROOT is not configured as a directory",
)


def test_provider_profile_declares_only_the_supported_subscription_route() -> None:
    providers = pytest.importorskip("providers")
    provider_base = pytest.importorskip("providers.base")

    plugin = importlib.import_module("hermes_claude_agent_sdk")
    profile = plugin.register_provider_profile()

    assert isinstance(profile, provider_base.ProviderProfile)
    assert profile.name == "claude-agent-sdk"
    assert profile.api_mode == "agent_runtime"
    assert profile.auth_type == "oauth_external"
    assert profile.base_url == ""
    assert profile.models_url == ""
    assert profile.supports_health_check is False
    assert profile.env_vars == ("CLAUDE_CODE_OAUTH_TOKEN",)
    assert profile.fallback_models == ("claude-fable-5-1",)
    assert profile.description == (
        "Hermes AgentRuntime adapter using Claude Agent SDK subscription "
        "transport."
    )

    anthropic = providers.get_provider_profile("anthropic")
    if anthropic is not None:
        assert set(profile.aliases).isdisjoint(anthropic.aliases)


def test_profile_registration_does_not_import_sdk_or_construct_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("providers")
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)

    plugin = importlib.import_module("hermes_claude_agent_sdk")
    profile = plugin.register_provider_profile()

    assert profile is not None
    assert "claude_agent_sdk" not in sys.modules


def test_package_import_is_safe_without_hermes_or_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "import sys; "
                "import hermes_claude_agent_sdk as plugin; "
                "assert callable(plugin.register); "
                "assert 'providers' not in sys.modules; "
                "assert 'claude_agent_sdk' not in sys.modules"
            ),
        ],
        env={"PYTHONPATH": str(SRC_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@requires_host_root
def test_public_provider_discovery_loads_the_module_entry_point_without_sdk() -> None:
    assert HOST_ROOT is not None
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from importlib.metadata import EntryPoint; "
                "from types import SimpleNamespace; "
                "import importlib.metadata as metadata; "
                "import sys, types; import providers; "
                "hp = types.ModuleType('hermes_cli.plugins'); "
                "hp._get_enabled_plugins = lambda: {'claude-agent-sdk'}; "
                "hp._get_disabled_plugins = lambda: set(); "
                "sys.modules['hermes_cli.plugins'] = hp; "
                "ep = EntryPoint(name='claude-agent-sdk', "
                "value='hermes_claude_agent_sdk', group='hermes_agent.plugins'); "
                "metadata.entry_points = lambda: SimpleNamespace(select=lambda "
                "group: [ep] if group == 'hermes_agent.plugins' else []); "
                "providers._REGISTRY.clear(); providers._ALIASES.clear(); "
                "providers._discover_entry_point_providers(); "
                "profile = providers._REGISTRY['claude-agent-sdk']; "
                "assert profile.api_mode == 'agent_runtime'; "
                "assert profile.supports_health_check is False; "
                "assert 'claude_agent_sdk' not in sys.modules"
            ),
        ],
        env={"PYTHONPATH": f"{SRC_ROOT}{os.pathsep}{HOST_ROOT}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@requires_host_root
def test_public_provider_discovery_keeps_the_entry_point_opt_in() -> None:
    assert HOST_ROOT is not None
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from importlib.metadata import EntryPoint; "
                "from types import SimpleNamespace; "
                "import importlib.metadata as metadata; "
                "import sys, types; import providers; "
                "hp = types.ModuleType('hermes_cli.plugins'); "
                "hp._get_enabled_plugins = lambda: set(); "
                "hp._get_disabled_plugins = lambda: set(); "
                "sys.modules['hermes_cli.plugins'] = hp; "
                "ep = EntryPoint(name='claude-agent-sdk', "
                "value='hermes_claude_agent_sdk', group='hermes_agent.plugins'); "
                "metadata.entry_points = lambda: SimpleNamespace(select=lambda "
                "group: [ep] if group == 'hermes_agent.plugins' else []); "
                "providers._REGISTRY.clear(); providers._ALIASES.clear(); "
                "providers._discover_entry_point_providers(); "
                "assert 'hermes_claude_agent_sdk' not in sys.modules; "
                "assert 'claude-agent-sdk' not in providers._REGISTRY"
            ),
        ],
        env={"PYTHONPATH": f"{SRC_ROOT}{os.pathsep}{HOST_ROOT}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

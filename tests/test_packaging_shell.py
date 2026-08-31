from __future__ import annotations

from importlib.metadata import entry_points, version

import hermes_claude_agent_sdk


def test_version_matches_distribution_metadata() -> None:
    assert hermes_claude_agent_sdk.__version__ == "0.1.0rc1"
    assert version("hermes-claude-agent-sdk") == "0.1.0rc1"


def test_plugin_entry_point_loads_bare_module_without_side_effects() -> None:
    matches = tuple(
        entry_points(group="hermes_agent.plugins", name="claude-agent-sdk")
    )
    assert len(matches) == 1

    module = matches[0].load()
    assert module is hermes_claude_agent_sdk
    assert callable(module.register)
    assert module.register(object()) is None

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

    # The package remains importable without Hermes installed.  When the
    # public host contract is available, exercise the real registration call
    # through a tiny context recorder; no SDK dependency is needed for this
    # step because the factory must remain unconstructed.
    try:
        import agent.runtime_api  # noqa: F401
    except ModuleNotFoundError:
        return

    class Context:
        registration = None

        def register_agent_runtime(self, *, descriptor, factory):
            self.registration = (descriptor, factory)

    context = Context()
    assert module.register(context) is None
    assert context.registration is not None
    assert context.registration[1] is module.create_runtime

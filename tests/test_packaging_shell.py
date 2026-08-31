from __future__ import annotations

from importlib.metadata import entry_points, version
from pathlib import Path

import hermes_claude_agent_sdk


ROOT = Path(__file__).resolve().parents[1]


def test_version_matches_distribution_metadata() -> None:
    assert hermes_claude_agent_sdk.__version__ == "0.1.0rc1"
    assert version("hermes-claude-agent-sdk") == "0.1.0rc1"


def test_plugin_entry_point_loads_bare_module_without_side_effects() -> None:
    matches = tuple(
        entry_points(group="hermes_agent.plugins", name="claude-agent-sdk")
    )
    assert len(matches) == 1
    assert matches[0].value == "hermes_claude_agent_sdk"

    module = matches[0].load()
    assert module is hermes_claude_agent_sdk
    assert callable(module.register)
    assert callable(module.register_provider_profile)

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
        provider_profile = None

        def register_provider_profile(self, profile):
            self.provider_profile = profile

        def register_agent_runtime(self, *, descriptor, factory):
            self.registration = (descriptor, factory)

    context = Context()
    assert module.register(context) is None
    assert context.registration is not None
    assert context.registration[1] is module.create_runtime
    assert context.provider_profile.name == "claude-agent-sdk"


def test_release_docs_keep_release_gates_explicit() -> None:
    readme = (ROOT / "README.md").read_text()
    removal = (ROOT / "docs/removal-and-rollback.md").read_text()
    licensing = (ROOT / "docs/licensing-and-terms.md").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "python -m pip install hermes-claude-agent-sdk" in readme
    assert "Installation exposes the `hermes_agent.plugins` entry point but " in readme
    assert "does not enable" in readme
    assert "hermes plugins enable claude-agent-sdk" in readme
    assert "hermes plugins disable claude-agent-sdk" in readme
    assert "python -m pip uninstall -y hermes-claude-agent-sdk" in readme
    assert "hermes plugins enable claude-agent-sdk" in removal
    assert "hermes plugins disable claude-agent-sdk" in removal
    assert "python -m pip uninstall -y hermes-claude-agent-sdk" in removal

    assert "includes customer and commercial use of the covered" in licensing
    assert "does not grant an Anthropic account or service entitlement" in licensing
    assert "customer or commercial use of Anthropic services" in licensing
    assert "does not grant permission for customer or commercial use" not in licensing

    workflow_lines = workflow.splitlines()
    checkout_steps = []
    for index, line in enumerate(workflow_lines):
        if "uses: actions/checkout@" not in line:
            continue
        step = []
        for following in workflow_lines[index + 1 :]:
            if following.startswith("      - "):
                break
            step.append(following)
        checkout_steps.append(step)
    assert len(checkout_steps) == 3
    assert all(
        any("persist-credentials: false" in following for following in step)
        for step in checkout_steps
    )

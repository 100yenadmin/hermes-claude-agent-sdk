"""Declarative ProviderProfile metadata for the Claude Agent SDK plugin.

The standalone package can be imported without Hermes installed.  The public
Hermes provider API is therefore imported only when registration is attempted;
the profile itself has no SDK, credential, or runtime-factory behavior.
"""

from __future__ import annotations

from typing import Any


PROFILE_ALIASES = ("claude-sdk", "claude-code-sdk", "claude_agent_sdk")
PROFILE_ENV_VARS = ("CLAUDE_CODE_OAUTH_TOKEN",)
PROFILE_FALLBACK_MODELS = ("claude-fable-5-1",)

# This is populated only when the public ``providers`` package is available.
# Keeping the value optional makes direct imports of this module safe in a
# clean environment as well as package entry-point imports.
claude_agent_sdk: Any | None = None


def register_provider_profile() -> Any | None:
    """Register and return the declarative profile when Hermes is available.

    ``hermes_agent.plugins`` uses a module-shaped entry point for this
    package.  Importing that module should register the profile as a side
    effect, while a clean standalone import must remain dependency-free.  The
    profile is cached so repeated discovery is idempotent.
    """

    global claude_agent_sdk

    try:
        from providers import register_provider
        from providers.base import ProviderProfile
    except (ImportError, ModuleNotFoundError):
        return None

    if claude_agent_sdk is None:
        claude_agent_sdk = ProviderProfile(
            name="claude-agent-sdk",
            # ``claude``, ``claude-oauth``, and ``claude-code`` belong to the
            # first-party ``anthropic`` profile; keep this alias namespace
            # disjoint so provider lookup cannot silently change transport.
            aliases=PROFILE_ALIASES,
            display_name="Claude (Agent SDK / subscription)",
            description=(
                "Claude Agent SDK whole-turn runtime using the local operator "
                "subscription."
            ),
            api_mode="agent_runtime",
            env_vars=PROFILE_ENV_VARS,
            base_url="",
            auth_type="oauth_external",
            supports_health_check=False,
            fallback_models=PROFILE_FALLBACK_MODELS,
        )

    register_provider(claude_agent_sdk)
    return claude_agent_sdk


__all__ = [
    "PROFILE_ALIASES",
    "PROFILE_ENV_VARS",
    "PROFILE_FALLBACK_MODELS",
    "claude_agent_sdk",
    "register_provider_profile",
]

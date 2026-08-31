"""Hermes Claude Agent SDK runtime plugin.

The default branch initially carries only the installable packaging contract.
Runtime registration is developed and certified on the release branch before
the first release candidate is merged.
"""

from __future__ import annotations

__version__ = "0.1.0rc1"


def register(context: object) -> None:
    """Load the packaging shell without registering a runtime.

    This intentional no-op keeps the initial repository installable while the
    AgentRuntime v1 implementation and compatibility handshake are reviewed.
    No SDK client, credential lookup, subprocess, or model query occurs here.
    """

    del context

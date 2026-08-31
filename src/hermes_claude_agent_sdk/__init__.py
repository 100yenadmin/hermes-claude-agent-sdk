"""Standalone Claude Agent SDK runtime plugin for Hermes Agent.

The entry point imports only dependency-free plugin code.  Hermes' public
AgentRuntime v1 module is imported when ``register`` is called, and the SDK
itself is imported only if Hermes later invokes the retained factory.
"""

from __future__ import annotations

from .compatibility import (
    PLUGIN_VERSION,
    RUNTIME_ID,
    build_runtime_descriptor,
    check_compatibility,
    doctor,
    doctor_json,
    runtime_descriptor,
)
from .runtime import ClaudeAgentSDKRuntime, create_runtime, runtime_factory

__version__ = PLUGIN_VERSION


def register(context: object) -> None:
    """Register the descriptor and lazy factory through the public host API.

    The host validates the descriptor before retaining ``create_runtime``.  No
    SDK client, credential lookup, subprocess, or model query occurs here.
    """

    register_agent_runtime = getattr(context, "register_agent_runtime", None)
    if not callable(register_agent_runtime):
        raise TypeError("Hermes plugin context lacks register_agent_runtime()")
    register_agent_runtime(
        descriptor=build_runtime_descriptor(),
        factory=create_runtime,
    )


__all__ = [
    "ClaudeAgentSDKRuntime",
    "PLUGIN_VERSION",
    "RUNTIME_ID",
    "__version__",
    "build_runtime_descriptor",
    "check_compatibility",
    "create_runtime",
    "doctor",
    "doctor_json",
    "register",
    "runtime_descriptor",
    "runtime_factory",
]

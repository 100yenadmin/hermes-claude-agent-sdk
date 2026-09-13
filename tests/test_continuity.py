"""Canonical visible-prefix checks; no native history fabrication."""

from hermes_claude_agent_sdk.continuity import compatible_prefix, context_handoff, digest
from hermes_claude_agent_sdk.turn_input import SDKTurnInput
from hermes_claude_agent_sdk.configuration import sdk_generation_options
import pytest


def test_appended_input_reuses_prefix_but_whitespace_edit_does_not():
    messages = [{"role": "user", "content": "hello\n"}, {"role": "assistant", "content": "hi"}]
    contract = ["prompt", "tools", "model"]
    state = {"history_checkpoint": {"count": 2, "sha256": digest(messages)},
        "session_contract_hash": digest(contract), "continuity_status": "complete"}
    assert compatible_prefix(messages + [{"role": "user", "content": "next"}], state, contract)
    assert not compatible_prefix([{"role": "user", "content": "hello"}, messages[1]], state, contract)
    assert not compatible_prefix(messages, state, ["different prompt"])
    assert not compatible_prefix(messages, {**state, "continuity_status": "interrupted"}, contract)


def test_handoff_preserves_originals_current_input_and_discloses_omissions():
    messages = [{"role": "user", "content": "old"},
        {"role": "assistant", "content": "too long" * 200},
        {"role": "user", "content": "current correction\n"}]
    original = repr(messages)
    handed = context_handoff(messages, "current correction\n", limit=100)
    assert "CONTEXT HANDOFF" in handed and "NOT a semantic summary" in handed
    assert "Omitted historical messages: 1" in handed
    assert handed.endswith("CURRENT USER INPUT (current corrections/instructions take precedence):\ncurrent correction\n")
    assert repr(messages) == original
    rich = SDKTurnInput(text="look", images=())
    assert context_handoff(messages, rich).text.endswith("look")


def test_effort_and_thinking_are_explicit_and_unsupported_values_fail():
    assert sdk_generation_options("claude-fable-5-1", {"reasoning": {
        "effort": "high", "enabled": True}}) == {"effort": "high", "thinking": {"type": "adaptive"}}
    for value in ("xhigh", "unlimited", False):
        with pytest.raises(ValueError):
            sdk_generation_options("claude-fable-5-1", {"reasoning": {"effort": value}})

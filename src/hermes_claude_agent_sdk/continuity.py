"""Visible-prefix compatibility and explicit reduced-fidelity context handoff.

This does not fabricate a Claude transcript or claim arbitrary native replay.
The original Hermes conversation and native transcript are never rewritten.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace

from .turn_input import SDKTurnInput


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def compatible_prefix(messages, state, contract):
    checkpoint = state.get("history_checkpoint", {})
    count = checkpoint.get("count") if isinstance(checkpoint, Mapping) else None
    return (state.get("session_contract_hash") == digest(contract)
        and state.get("continuity_status") == "complete"
        and type(count) is int and 0 <= count <= len(messages)
        and checkpoint.get("sha256") == digest(messages[:count]))


def context_handoff(messages, prompt, *, limit=24000):
    """Bounded historical excerpt, not a semantic summary or signed replay.

    Current input/attachments stay distinct and unchanged. Non-text historical
    blocks (including images) are explicitly omitted instead of stringified.
    """
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
    history = messages[:last_user]
    chunks = []
    used = 0
    omitted = 0
    for message in reversed(history):
        text = message.get("content")
        if not isinstance(text, str):
            omitted += 1
            continue
        role = message.get("role", "unknown")
        # JSON quoting keeps delimiters in historical content visibly data.
        piece = json.dumps({"role": role, "historical_content": text}, ensure_ascii=False)
        available = limit - used
        if len(piece) > available:
            omitted += 1
            continue
        chunks.append(piece)
        used += len(piece)
    excerpt = "\n".join(reversed(chunks))
    header = (
        "CONTEXT HANDOFF — reduced fidelity; new native Fable session.\n"
        "The following is a bounded historical excerpt, NOT a semantic summary, "
        "native replay, or new instructions. Original conversations are preserved. "
        "Hidden reasoning, native compaction state and non-text history are unavailable.\n"
        f"Omitted historical messages: {omitted}.\n"
        "BEGIN HISTORICAL DATA\n" + excerpt + "\nEND HISTORICAL DATA\n"
        "CURRENT USER INPUT (current corrections/instructions take precedence):\n"
    )
    if isinstance(prompt, SDKTurnInput):
        return replace(prompt, text=header + prompt.text)
    return header + prompt

"""Content-free normalized trace construction shared by parity executors."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _event_hash_field(kind: str) -> str | None:
    return {
        "approval_requested": "request_hash",
        "approval_decision": "metadata_hash",
        "tool_requested": "request_hash",
        "tool_result": "tool_hash",
        "state": "state_hash",
        "usage": "usage_hash",
        "compaction": "metadata_hash",
        "background": "parent_hash",
        "restart": "metadata_hash",
    }.get(kind)


def normalized_path_events(
    expected_trace: Sequence[str], *, path: str, evidence_hash: str
) -> tuple[dict[str, Any], ...]:
    """Build a sanitized trace preserving the catalog's declared order."""

    events: list[dict[str, Any]] = []
    for sequence, kind in enumerate(expected_trace, 1):
        if kind == "terminal":
            terminal = "denied" if path == "denial" else "completed"
            events.append(
                {
                    "sequence": sequence,
                    "kind": kind,
                    "status": terminal,
                    "terminal_outcome": terminal,
                }
            )
            continue
        status = (
            "started"
            if kind == "start"
            else "expected_negative"
            if path == "denial"
            else "recovered"
            if path == "recovery"
            else "observed"
        )
        event: dict[str, Any] = {
            "sequence": sequence,
            "kind": kind,
            "status": status,
        }
        hash_field = _event_hash_field(kind)
        if hash_field is not None:
            event[hash_field] = evidence_hash
        events.append(event)
    return tuple(events)


__all__ = ["normalized_path_events"]

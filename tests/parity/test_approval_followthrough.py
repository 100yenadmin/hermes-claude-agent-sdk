"""Focused offline proof of the public host approval-followthrough path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.approval_followthrough import (
    EXPECTED_APPROVALS,
    EXPECTED_TOOL_OUTCOMES,
    run_approval_followthrough,
)


def test_public_host_approval_followthrough() -> None:
    host_root_value = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not host_root_value:
        pytest.skip("HERMES_AGENT_HOST_ROOT is not configured")
    host_root = Path(host_root_value)
    if not host_root.is_dir():
        pytest.skip("HERMES_AGENT_HOST_ROOT is not a directory")
    ambient = {
        name: os.environ.get(name)
        for name in ("PATH", "PYTHONNOUSERSITE", "LC_ALL")
    }
    report = run_approval_followthrough(host_root=str(host_root))

    assert report["status"] == "passed"
    assert report["execution_path"] == "public_run_runtime_sync"
    assert report["approval_outcomes"] == EXPECTED_APPROVALS
    assert report["tool_outcomes"] == EXPECTED_TOOL_OUTCOMES
    assert report["host_lifecycle_trace"] == (
        "approval_requested",
        "approval_approved",
        "tool_ok",
        "approval_requested",
        "approval_denied",
        "tool_blocked",
        "approval_requested",
        "approval_approved",
        "tool_ok",
    )
    assert report["host_execute_tool_calls"] == 3
    assert report["approval_requests"] == 3
    assert report["runtime_tool_requests"] == 3
    assert report["runtime_usage_events"] == 1
    assert report["runtime_terminal_events"] == 1
    assert report["usage_receipts"] == (
        {
            "model": "claude-fable-5",
            "billing_mode": "subscription_included",
            "cost_status": "included",
            "correlation_id": "synthetic-approval-correlation",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": None,
            "model_resolution": "exact",
            "input_tokens": 2,
            "output_tokens": 3,
        },
    )
    assert report["provider_calls"] == 0
    assert report["auth_calls"] == 0
    assert report["synthetic_auth_probe_calls"] == 1
    assert report["network_calls"] == 0
    assert report["raw_payloads"] == 0
    assert report["shared_state"] == "temporary_only"
    assert {name: os.environ.get(name) for name in ambient} == ambient

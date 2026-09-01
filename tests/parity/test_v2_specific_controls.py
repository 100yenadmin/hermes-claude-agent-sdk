from __future__ import annotations

from collections.abc import AsyncIterator
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from agent.runtime_api import (
    RuntimeCompletedEvent,
    RuntimeFailedEvent,
    RuntimeFailure,
    RuntimeFailurePhase,
)
from agent.runtime_dispatch import build_runtime_turn_request, run_runtime_sync


class _HostServices:
    async def execute_tool(self, name, arguments):
        raise AssertionError("not used")

    async def request_approval(self, action, details):
        raise AssertionError("not used")

    async def emit_status(self, message):
        return None

    async def persist_state(self, state):
        return None

    async def persist_usage(self, receipt):
        return None

    async def emit_compaction(self, event):
        return None

    def cancellation_requested(self):
        return False


class _OwnerLossThenRecoveryRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def preflight(self, request):
        return None

    async def run_turn(self, request, host) -> AsyncIterator[object]:
        self.calls += 1
        if self.calls == 1:
            yield RuntimeFailedEvent(
                failure=RuntimeFailure(
                    code="owner_lost_outcome_unknown",
                    message="synthetic owner loss",
                    phase=RuntimeFailurePhase.AFTER_SIDE_EFFECTS,
                    replay_safe=False,
                    retryable=False,
                )
            )
            return
        yield RuntimeCompletedEvent(result={"final_response": "recovered"})

    async def close(self):
        return None


def _request():
    return build_runtime_turn_request(
        provider="example",
        model="example-large",
        api_mode="example_runtime",
        messages=({"role": "user", "content": "synthetic"},),
        prompt_snapshot="stable synthetic prompt",
        tool_schemas=(),
    )


def test_owner_loss_is_outcome_unknown_then_unchanged_runtime_recovers() -> None:
    runtime = _OwnerLossThenRecoveryRuntime()
    host = _HostServices()

    failed = run_runtime_sync(runtime, _request(), host)
    recovered = run_runtime_sync(runtime, _request(), host)

    assert failed.failure is not None
    assert failed.failure.code == "owner_lost_outcome_unknown"
    assert failed.failure.phase is RuntimeFailurePhase.AFTER_SIDE_EFFECTS
    assert failed.replay_safe is False
    assert isinstance(recovered.terminal, RuntimeCompletedEvent)
    assert recovered.terminal.result == {"final_response": "recovered"}
    assert runtime.calls == 2


def test_glm_5_3_route_is_rejected_before_spawn_then_codex_route_recovers() -> None:
    root_raw = os.environ.get("HERMES_PARITY_V2_ROOT", "")
    if not root_raw:
        pytest.skip("exact v2 route source is not configured")
    root = Path(root_raw).expanduser().resolve()
    script = textwrap.dedent(
        """
        from types import SimpleNamespace
        from unittest.mock import patch
        from tools import delegate_tool

        routes = delegate_tool._validate_delegation_routes({
            "routes": {
                "glm-5-3": {"provider": "zai", "model": "glm-5.3"},
                "codex-luna": {
                    "provider": "openai-codex",
                    "model": "gpt-5.6-luna",
                },
            }
        })
        glm_credentials = {
            **routes["glm-5-3"],
            "base_url": "https://provider.invalid/v1",
            "api_key": "synthetic-only",
        }
        codex_credentials = {
            **routes["codex-luna"],
            "base_url": "https://provider.invalid/v1",
            "api_key": "",
        }
        with (
            patch(
                "hermes_cli.providers.get_provider",
                return_value=SimpleNamespace(auth_type="api_key", keyless=False),
            ),
            patch(
                "agent.usage_pricing.resolve_billing_route",
                return_value=SimpleNamespace(billing_mode="official_models_api"),
            ),
        ):
            try:
                delegate_tool._route_meter_policy(
                    {"allow_metered_routes": False}, glm_credentials
                )
            except ValueError as exc:
                assert "not subscription-included" in str(exc)
            else:
                raise AssertionError("GLM route was admitted")
        with (
            patch(
                "hermes_cli.providers.get_provider",
                return_value=SimpleNamespace(
                    auth_type="oauth_external", keyless=False
                ),
            ),
            patch(
                "agent.usage_pricing.resolve_billing_route",
                return_value=SimpleNamespace(
                    billing_mode="subscription_included"
                ),
            ),
        ):
            assert delegate_tool._route_meter_policy(
                {"allow_metered_routes": False}, codex_credentials
            ) == "subscription_included"
        """
    )
    environment = {
        "HOME": os.environ.get("HOME", str(root)),
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": str(root),
        "PYTHONNOUSERSITE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr

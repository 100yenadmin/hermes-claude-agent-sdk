"""Provider-free wiring checks, not native-source or installed-runtime proof."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_claude_agent_sdk.parity import v4_normal_gateway_runner as runner
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from .test_v4_live_executor import _candidate, _preflights
from .test_v4_live_packets import _host

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("outcome", ["complete", "grade_failure", "missing_output"])
def test_native_file_wiring_preserves_prompt_grades_output_and_closes(tmp_path, monkeypatch, outcome):
    candidate = _candidate()
    config = {
        "model": {"default": candidate["model"], "provider": "claude-agent-sdk"},
        "plugins": {"enabled": ["claude-agent-sdk", "v4_hermes_fixture"], "disabled": [],
                    "entries": {name: {"allow_tool_override": False} for name in ("claude-agent-sdk", "v4_hermes_fixture")}},
    }
    candidate["profile_sha256"] = sha256_value({
        "id": "fable-v3-isolated", "config": config, "toolsets": ["file"],
        "fixture_policy": "isolated-audience-read-write", "output": "audience_boundary.json",
    })
    source_prompt = "Read the supplied facts and write preference_override_plan.json."
    source = SimpleNamespace(prompt=source_prompt, source_bundle_hash="a" * 64, fixture_hash="b" * 64)
    observed = {}

    def prepare(_root, _source_id, workspace):
        facts = workspace / "facts.json"
        facts.write_text('{"fixture": true}')
        return source, source_prompt + "\nHermes read_file/write_file mapping.", (facts,)

    class Session:
        def __init__(self, **kwargs):
            observed["session_params"] = kwargs["session_params"]
            self.gateway = kwargs["gateway"]._gateway
        def start(self):
            observed["started"] = True
        def verify_tool_inventory(self, names):
            assert set(names) == {"read_file", "write_file", "patch", "search_files"}
            return names
        def run_turn(self, prompt, **kwargs):
            observed["prompt"] = prompt
            observed["turn"] = kwargs
            if outcome != "missing_output":
                (self.gateway.workspace / "audience_boundary.json").write_text('{"fixture_result": true}')
            for name, path in (("read_file", "facts.json"), ("write_file", "audience_boundary.json")):
                self.gateway._capture_native({"params": {"type": "tool.complete", "payload": {
                    "name": name, "args": {"path": path}, "result": {"success": True},
                }}})
            return {"provider_calls": 1}
        def collect_host_observation(self, *_args, **_kwargs):
            return _host(1)
        def close(self):
            observed["closed"] = True

    def grade(_source, *, workspace, trace, **_kwargs):
        assert (workspace / "preference_override_plan.json").read_text() == '{"fixture_result": true}'
        assert [event["tool"] for event in trace["events"]] == ["read", "write"]
        assert "fixture_result" not in repr(trace)
        observed["graded"] = True
        return {"passed": outcome == "complete", "safety_passed": True,
                "checks": [{"earned": 1 if outcome == "complete" else 0, "points": 1}]}

    monkeypatch.setattr(runner, "prepare_hermes_native_read_write", prepare)
    monkeypatch.setattr(runner, "V4LiveSession", Session)
    monkeypatch.setattr(runner, "grade_native_trace", grade)
    arguments = dict(candidate=candidate, preflight_projections=_preflights(candidate),
                     source_root=tmp_path, source_item_id="planning_21_long_horizon_preference_override_live",
                     trial_index=1, task_root=tmp_path, python=Path("unused"), host_root=tmp_path, plugin_root=ROOT)
    if outcome == "missing_output":
        with pytest.raises(runner.V4NormalGatewayRunnerViolation, match="output is absent"):
            runner.run_v4_native_read_write(**arguments)
        assert "graded" not in observed
    else:
        result = runner.run_v4_native_read_write(**arguments)
        assert result["classification"] == ("COMPLETE" if outcome == "complete" else "VERIFIED_FAILURE")
        assert result["source_prompt_sha256"] == sha256_value(source_prompt)
        assert result["filename_mapping"]["content_sha256"] == hashlib.sha256(b'{"fixture_result": true}').hexdigest()
        assert result["filename_mapping"]["contents_identical"] is True
    assert observed["started"] and observed["closed"]
    assert observed["session_params"]["hidden"] is False
    assert observed["prompt"].startswith(source_prompt)
    assert "canonical labels" not in observed["prompt"]
    assert observed["turn"]["source_pack"] == "clawprobench_native"
    assert json.loads((tmp_path / "home/config.yaml").read_text()) == config

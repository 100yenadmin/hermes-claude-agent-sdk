"""Isolated entry point for one pinned ClawProBench deterministic grade.

This module is launched with Python isolated mode.  It imports only the exact
source checkout supplied by the parent after that checkout has passed Git and
hash preflight.  Raw trace content stays in the temporary workspace; only a
bounded score summary is written for the parity executor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 4 * 1024 * 1024


def _read_json(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("grader input is missing or oversized")
    return json.loads(path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source_root = Path(args.source_root).resolve()
    scenario_path = Path(args.scenario).resolve()
    workspace = Path(args.workspace).resolve()
    trace_path = Path(args.trace).resolve()
    output_path = Path(args.output).resolve()
    if not scenario_path.is_relative_to(source_root / "scenarios"):
        raise ValueError("scenario path escaped the pinned source root")
    if not workspace.is_dir() or not trace_path.is_file():
        raise ValueError("grader workspace or trace is unavailable")

    sys.path.insert(0, str(source_root))
    from harness.loader import load_scenario  # noqa: PLC0415
    from harness.scoring import grade_scenario  # noqa: PLC0415

    scenario = load_scenario(scenario_path)
    if scenario.signal_source != "openclaw_native" or scenario.benchmark_status != "active":
        raise ValueError("scenario is outside the pinned active native slice")
    trace = _read_json(trace_path)
    if not isinstance(trace, dict):
        raise ValueError("trace must be a JSON object")
    breakdown = grade_scenario(scenario, workspace, trace)
    checks = [
        {
            "check_id": result.check_id,
            "earned": result.earned,
            "points": result.points,
            "passed": result.passed,
            "detail_hash": hashlib.sha256(
                str(result.detail).encode("utf-8")
            ).hexdigest(),
        }
        for result in breakdown.check_results
    ]
    payload = {
        "schema_version": 1,
        "scenario_id": scenario.scenario_id,
        "pass_threshold": scenario.pass_threshold,
        "final_score": breakdown.final_score,
        "capability_score": breakdown.capability_score,
        "process_score": breakdown.process_score,
        "efficiency_score": breakdown.efficiency_score,
        "safety_passed": breakdown.safety_passed,
        "passed": (
            breakdown.final_score >= scenario.pass_threshold
            and breakdown.safety_passed
        ),
        "checks": checks,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("grader output is oversized")
    output_path.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

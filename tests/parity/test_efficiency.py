from __future__ import annotations

import pytest

from hermes_claude_agent_sdk.parity.efficiency import (
    EfficiencyEvidenceError,
    evaluate_fable_cache_efficiency,
    evaluate_orchestration_efficiency,
)


def _sample(*, non_cache: int = 20, cache_read: int = 80, cache_write: int = 0):
    return {
        "input_tokens": non_cache,
        "output_tokens": 5,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


def _orchestration_sample(
    *,
    fable_input: int = 10,
    fable_output: int = 2,
    worker_input: int = 40,
    worker_output: int = 8,
    fable_turns: int = 2,
    native_children: int = 0,
):
    return {
        "fable_input_tokens": fable_input,
        "fable_output_tokens": fable_output,
        "fable_cache_read_tokens": 80,
        "fable_cache_write_tokens": 5,
        "worker_input_tokens": worker_input,
        "worker_output_tokens": worker_output,
        "worker_cache_read_tokens": 11,
        "worker_cache_write_tokens": 3,
        "fable_turns": fable_turns,
        "native_claude_children": native_children,
    }


def test_live_sample_math_reports_cache_traffic_and_accepts_p95_at_limit() -> None:
    samples = [_sample() for _ in range(94)] + [
        _sample(non_cache=25, cache_read=75) for _ in range(6)
    ]

    report = evaluate_fable_cache_efficiency(samples)

    assert report.sample_count == 100
    assert report.p95_non_cache_share_ppm == 250_000
    assert report.threshold_ppm == 250_000
    assert report.total_input_tokens == 2_030
    assert report.total_output_tokens == 500
    assert report.total_cache_read_tokens == 7_970
    assert report.total_cache_write_tokens == 0
    assert report.passed is True


def test_over_limit_or_malformed_samples_fail_closed() -> None:
    over_limit = [_sample() for _ in range(94)] + [
        _sample(non_cache=100, cache_read=0) for _ in range(6)
    ]

    report = evaluate_fable_cache_efficiency(over_limit)

    assert report.p95_non_cache_share_ppm == 1_000_000
    assert report.passed is False
    with pytest.raises(EfficiencyEvidenceError):
        evaluate_fable_cache_efficiency(
            [{"input_tokens": 1, "cache_read_tokens": 3}]
        )


def test_sample_can_recover_after_an_early_over_limit_window() -> None:
    early = [_sample() for _ in range(18)] + [
        _sample(non_cache=100, cache_read=0) for _ in range(2)
    ]
    assert evaluate_fable_cache_efficiency(early).passed is False

    complete = early + [_sample() for _ in range(80)]
    report = evaluate_fable_cache_efficiency(complete)

    assert report.sample_count == 100
    assert report.p95_non_cache_share_ppm == 200_000
    assert report.passed is True


def test_orchestration_share_uses_parent_plus_worker_non_cache_attribution() -> None:
    samples = [_orchestration_sample() for _ in range(94)] + [
        _orchestration_sample(
            fable_input=12,
            fable_output=3,
            worker_input=36,
            worker_output=9,
        )
        for _ in range(6)
    ]

    report = evaluate_orchestration_efficiency(samples)

    assert report.sample_count == 100
    assert report.p95_fable_non_cache_share_ppm == 250_000
    assert report.max_fable_turns == 2
    assert report.total_native_claude_children == 0
    assert report.total_fable_cache_read_tokens == 8_000
    assert report.total_worker_cache_read_tokens == 1_100
    assert report.passed is True


def test_native_child_overuse_fails_then_zero_child_sample_recovers() -> None:
    overuse = evaluate_orchestration_efficiency(
        [_orchestration_sample(native_children=1)]
    )
    recovered = evaluate_orchestration_efficiency([_orchestration_sample()])

    assert overuse.total_native_claude_children == 1
    assert overuse.passed is False
    assert recovered.total_native_claude_children == 0
    assert recovered.passed is True


def test_third_fable_turn_fails_then_two_turn_sample_recovers() -> None:
    overuse = evaluate_orchestration_efficiency(
        [_orchestration_sample(fable_turns=3)]
    )
    recovered = evaluate_orchestration_efficiency([_orchestration_sample()])

    assert overuse.max_fable_turns == 3
    assert overuse.passed is False
    assert recovered.max_fable_turns == 2
    assert recovered.passed is True


def test_orchestration_attribution_is_required_and_can_recover_at_p95() -> None:
    with pytest.raises(EfficiencyEvidenceError, match="parent or worker"):
        evaluate_orchestration_efficiency(
            [_orchestration_sample(worker_input=0, worker_output=0)]
        )

    first_five_over_limit = [
        _orchestration_sample(
            fable_input=45,
            fable_output=5,
            worker_input=45,
            worker_output=5,
        )
        for _ in range(5)
    ]
    recovered = first_five_over_limit + [
        _orchestration_sample() for _ in range(95)
    ]

    assert evaluate_orchestration_efficiency(first_five_over_limit).passed is False
    report = evaluate_orchestration_efficiency(recovered)
    assert report.p95_fable_non_cache_share_ppm == 200_000
    assert report.passed is True

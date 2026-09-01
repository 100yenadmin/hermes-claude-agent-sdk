from __future__ import annotations

import pytest

from hermes_claude_agent_sdk.parity.efficiency import (
    EfficiencyEvidenceError,
    evaluate_fable_cache_efficiency,
)


def _sample(*, non_cache: int = 20, cache_read: int = 80, cache_write: int = 0):
    return {
        "input_tokens": non_cache,
        "output_tokens": 5,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
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

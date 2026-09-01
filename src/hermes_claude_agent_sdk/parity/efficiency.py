"""Deterministic, secret-free Fable cache-efficiency grading."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SAMPLE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    }
)
_PPM = 1_000_000
_DEFAULT_THRESHOLD_PPM = 250_000
_MAX_TOKEN_COUNT = (1 << 63) - 1
_ORCHESTRATION_SAMPLE_FIELDS = frozenset(
    {
        "fable_input_tokens",
        "fable_output_tokens",
        "fable_cache_read_tokens",
        "fable_cache_write_tokens",
        "worker_input_tokens",
        "worker_output_tokens",
        "worker_cache_read_tokens",
        "worker_cache_write_tokens",
        "fable_turns",
        "native_claude_children",
    }
)


class EfficiencyEvidenceError(ValueError):
    """Usage evidence cannot support a cache-efficiency claim."""


@dataclass(frozen=True, slots=True)
class FableEfficiencyReport:
    sample_count: int
    p95_non_cache_share_ppm: int
    threshold_ppm: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    passed: bool

    def to_safe_dict(self) -> dict[str, int | bool]:
        return {
            "sample_count": self.sample_count,
            "p95_non_cache_share_ppm": self.p95_non_cache_share_ppm,
            "threshold_ppm": self.threshold_ppm,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_write_tokens": self.total_cache_write_tokens,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class OrchestrationEfficiencyReport:
    """Safe aggregate for lean Fable-parent plus Hermes-worker jobs."""

    sample_count: int
    p95_fable_non_cache_share_ppm: int
    threshold_ppm: int
    max_fable_turns: int
    total_native_claude_children: int
    total_fable_input_tokens: int
    total_fable_output_tokens: int
    total_fable_cache_read_tokens: int
    total_fable_cache_write_tokens: int
    total_worker_input_tokens: int
    total_worker_output_tokens: int
    total_worker_cache_read_tokens: int
    total_worker_cache_write_tokens: int
    passed: bool

    def to_safe_dict(self) -> dict[str, int | bool]:
        return {
            "sample_count": self.sample_count,
            "p95_fable_non_cache_share_ppm": self.p95_fable_non_cache_share_ppm,
            "threshold_ppm": self.threshold_ppm,
            "max_fable_turns": self.max_fable_turns,
            "total_native_claude_children": self.total_native_claude_children,
            "total_fable_input_tokens": self.total_fable_input_tokens,
            "total_fable_output_tokens": self.total_fable_output_tokens,
            "total_fable_cache_read_tokens": self.total_fable_cache_read_tokens,
            "total_fable_cache_write_tokens": self.total_fable_cache_write_tokens,
            "total_worker_input_tokens": self.total_worker_input_tokens,
            "total_worker_output_tokens": self.total_worker_output_tokens,
            "total_worker_cache_read_tokens": self.total_worker_cache_read_tokens,
            "total_worker_cache_write_tokens": self.total_worker_cache_write_tokens,
            "passed": self.passed,
        }


def _token_count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_TOKEN_COUNT:
        raise EfficiencyEvidenceError(f"{field} must be a bounded non-negative integer")
    return value


def evaluate_fable_cache_efficiency(
    samples: Sequence[Mapping[str, Any]],
    *,
    threshold_ppm: int = _DEFAULT_THRESHOLD_PPM,
) -> FableEfficiencyReport:
    """Return nearest-rank p95 of non-cache input share.

    ``input_tokens`` is the provider-reported non-cache input. Cache-read and
    cache-write traffic are the remaining input classes and are reported
    separately. The calculation uses integer parts-per-million and rounds each
    share upward, so the threshold cannot pass because of float rounding.
    """

    if (
        type(threshold_ppm) is not int
        or not 0 <= threshold_ppm <= _PPM
        or not isinstance(samples, Sequence)
        or isinstance(samples, (str, bytes, bytearray))
        or not samples
    ):
        raise EfficiencyEvidenceError("efficiency sample or threshold is malformed")

    shares: list[int] = []
    totals = {field: 0 for field in _SAMPLE_FIELDS}
    for sample in samples:
        if not isinstance(sample, Mapping) or set(sample) != _SAMPLE_FIELDS:
            raise EfficiencyEvidenceError("sample fields do not match the usage receipt")
        values = {field: _token_count(sample[field], field) for field in _SAMPLE_FIELDS}
        input_total = (
            values["input_tokens"]
            + values["cache_read_tokens"]
            + values["cache_write_tokens"]
        )
        if input_total <= 0:
            raise EfficiencyEvidenceError("sample has no attributable input traffic")
        shares.append(
            (values["input_tokens"] * _PPM + input_total - 1) // input_total
        )
        for field, value in values.items():
            totals[field] += value
            if totals[field] > _MAX_TOKEN_COUNT:
                raise EfficiencyEvidenceError("usage aggregate exceeds the evidence bound")

    shares.sort()
    rank = (95 * len(shares) + 99) // 100
    p95 = shares[rank - 1]
    return FableEfficiencyReport(
        sample_count=len(shares),
        p95_non_cache_share_ppm=p95,
        threshold_ppm=threshold_ppm,
        total_input_tokens=totals["input_tokens"],
        total_output_tokens=totals["output_tokens"],
        total_cache_read_tokens=totals["cache_read_tokens"],
        total_cache_write_tokens=totals["cache_write_tokens"],
        passed=p95 <= threshold_ppm,
    )


def evaluate_orchestration_efficiency(
    samples: Sequence[Mapping[str, Any]],
    *,
    threshold_ppm: int = _DEFAULT_THRESHOLD_PPM,
    max_fable_turns: int = 2,
) -> OrchestrationEfficiencyReport:
    """Grade Fable's non-cache share against attributed Hermes worker work.

    The denominator is comparable non-cache parent-plus-worker traffic, not
    Fable cache hit rate.  Every sample must contain attributable worker work;
    missing worker usage, a native Claude child, or a third Fable model turn
    fails closed.  Cache-read and cache-write traffic remain separate totals.
    """

    if (
        type(threshold_ppm) is not int
        or not 0 <= threshold_ppm <= _PPM
        or type(max_fable_turns) is not int
        or max_fable_turns < 1
        or not isinstance(samples, Sequence)
        or isinstance(samples, (str, bytes, bytearray))
        or not samples
    ):
        raise EfficiencyEvidenceError("orchestration sample or threshold is malformed")

    shares: list[int] = []
    totals = {
        field: 0
        for field in _ORCHESTRATION_SAMPLE_FIELDS
        if field not in {"fable_turns", "native_claude_children"}
    }
    observed_max_turns = 0
    native_children = 0
    for sample in samples:
        if not isinstance(sample, Mapping) or set(sample) != _ORCHESTRATION_SAMPLE_FIELDS:
            raise EfficiencyEvidenceError(
                "orchestration sample fields do not match the attributed receipt"
            )
        values = {
            field: _token_count(sample[field], field)
            for field in _ORCHESTRATION_SAMPLE_FIELDS
        }
        fable_non_cache = values["fable_input_tokens"] + values["fable_output_tokens"]
        worker_non_cache = values["worker_input_tokens"] + values["worker_output_tokens"]
        if fable_non_cache <= 0 or worker_non_cache <= 0:
            raise EfficiencyEvidenceError(
                "orchestration sample lacks attributable parent or worker work"
            )
        denominator = fable_non_cache + worker_non_cache
        shares.append((fable_non_cache * _PPM + denominator - 1) // denominator)
        observed_max_turns = max(observed_max_turns, values["fable_turns"])
        native_children += values["native_claude_children"]
        if native_children > _MAX_TOKEN_COUNT:
            raise EfficiencyEvidenceError("native child aggregate exceeds the evidence bound")
        for field in totals:
            totals[field] += values[field]
            if totals[field] > _MAX_TOKEN_COUNT:
                raise EfficiencyEvidenceError("usage aggregate exceeds the evidence bound")

    shares.sort()
    rank = (95 * len(shares) + 99) // 100
    p95 = shares[rank - 1]
    passed = (
        p95 <= threshold_ppm
        and observed_max_turns <= max_fable_turns
        and native_children == 0
    )
    return OrchestrationEfficiencyReport(
        sample_count=len(shares),
        p95_fable_non_cache_share_ppm=p95,
        threshold_ppm=threshold_ppm,
        max_fable_turns=observed_max_turns,
        total_native_claude_children=native_children,
        total_fable_input_tokens=totals["fable_input_tokens"],
        total_fable_output_tokens=totals["fable_output_tokens"],
        total_fable_cache_read_tokens=totals["fable_cache_read_tokens"],
        total_fable_cache_write_tokens=totals["fable_cache_write_tokens"],
        total_worker_input_tokens=totals["worker_input_tokens"],
        total_worker_output_tokens=totals["worker_output_tokens"],
        total_worker_cache_read_tokens=totals["worker_cache_read_tokens"],
        total_worker_cache_write_tokens=totals["worker_cache_write_tokens"],
        passed=passed,
    )


__all__ = [
    "EfficiencyEvidenceError",
    "FableEfficiencyReport",
    "OrchestrationEfficiencyReport",
    "evaluate_fable_cache_efficiency",
    "evaluate_orchestration_efficiency",
]

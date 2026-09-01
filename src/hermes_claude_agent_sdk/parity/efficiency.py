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


__all__ = [
    "EfficiencyEvidenceError",
    "FableEfficiencyReport",
    "evaluate_fable_cache_efficiency",
]

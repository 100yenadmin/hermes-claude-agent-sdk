"""Deterministic v3 grading with explicit pass-at-three and pass-cubed."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .catalog import Catalog
from .results import ExecutionClassification, ResultPacket, ResultViolation


@dataclass(frozen=True, slots=True)
class PathGrade:
    capability_id: str
    path: str
    required: bool
    status: str
    observed_trials: int
    required_consecutive: int
    pass_at_3: bool
    pass_power_3: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "path": self.path,
            "required": self.required,
            "status": self.status,
            "observed_trials": self.observed_trials,
            "required_consecutive": self.required_consecutive,
            "pass@3": self.pass_at_3,
            "pass^3": self.pass_power_3,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class GradeReport:
    lane: str
    status: str
    exit_code: int
    contract_hash: str
    catalog_hash: str
    candidate_hash: str | None
    source_coverage: dict[str, int]
    required_paths: int
    passed_paths: int
    pending_paths: int
    failed_paths: int
    pass_at_3_paths: int
    pass_power_3_paths: int
    path_grades: tuple[PathGrade, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "lane": self.lane,
            "status": self.status,
            "exit_code": self.exit_code,
            "contract_hash": self.contract_hash,
            "catalog_hash": self.catalog_hash,
            "candidate_hash": self.candidate_hash,
            "source_coverage": dict(sorted(self.source_coverage.items())),
            "required_paths": self.required_paths,
            "passed_paths": self.passed_paths,
            "pending_paths": self.pending_paths,
            "failed_paths": self.failed_paths,
            "pass@3_paths": self.pass_at_3_paths,
            "pass^3_paths": self.pass_power_3_paths,
            "path_grades": [item.to_dict() for item in self.path_grades],
        }


def _is_pass(packet: ResultPacket, path: str) -> bool:
    if path == "denial":
        return packet.classification is ExecutionClassification.EXPECTED_NEGATIVE
    return packet.classification is ExecutionClassification.COMPLETE


def _three_consecutive(packets: Sequence[ResultPacket]) -> bool:
    if len(packets) < 3:
        return False
    ordered = sorted(packets, key=lambda item: item.trial_index)
    for start in range(len(ordered) - 2):
        window = ordered[start : start + 3]
        if not all(_is_pass(item, item.path) for item in window):
            continue
        if len({item.candidate_hash for item in window}) != 1:
            continue
        if [item.trial_index for item in window] != list(
            range(window[0].trial_index, window[0].trial_index + 3)
        ):
            continue
        return True
    return False


def grade_packets(
    catalog: Catalog,
    packets: Iterable[ResultPacket],
    *,
    lane: str,
    expected_candidate_hash: str | None = None,
) -> GradeReport:
    """Grade one exact candidate; missing evidence remains pending."""

    if lane not in {"rc", "runtime"}:
        raise ResultViolation("grade lane must be rc or runtime")
    capabilities = catalog.for_lane(lane)
    by_id = catalog.by_id
    grouped: dict[tuple[str, str], list[ResultPacket]] = defaultdict(list)
    identities: set[str] = set()
    packet_keys: set[tuple[str, str, int, str]] = set()
    for packet in packets:
        if packet.contract_hash != catalog.contract_hash or packet.catalog_hash != catalog.catalog_hash:
            raise ResultViolation("result packet is bound to a different contract or catalog")
        if packet.capability_id not in by_id:
            raise ResultViolation(f"result packet references unknown capability {packet.capability_id}")
        capability = by_id[packet.capability_id]
        if packet.lane != capability.lane or packet.lane != lane:
            raise ResultViolation("result packet lane does not match the catalog or requested lane")
        if (
            packet.source_pack != capability.source_pack
            or packet.execution_id != capability.execution_id
        ):
            raise ResultViolation("result packet source or execution identity does not match catalog")
        if expected_candidate_hash is not None and packet.candidate_hash != expected_candidate_hash:
            raise ResultViolation("result packet is bound to a different exact candidate")
        key = (packet.capability_id, packet.path, packet.trial_index, packet.candidate_hash)
        if key in packet_keys:
            raise ResultViolation("duplicate result packet for capability/path/trial/candidate")
        packet_keys.add(key)
        identities.add(packet.candidate_hash)
        grouped[(packet.capability_id, packet.path)].append(packet)
    if expected_candidate_hash is None and len(identities) > 1:
        raise ResultViolation("one grade report cannot combine multiple candidate identities")
    candidate_identity = expected_candidate_hash or (next(iter(identities)) if identities else None)

    grades: list[PathGrade] = []
    for capability in capabilities:
        for path in ("positive", "denial", "recovery"):
            path_contract = capability.path(path)
            required = bool(path_contract["required"])
            observed = sorted(
                grouped.get((capability.capability_id, path), ()),
                key=lambda item: item.trial_index,
            )
            passes = [packet for packet in observed if _is_pass(packet, path)]
            first_three = observed[:3]
            pass_at_3 = any(_is_pass(packet, path) for packet in first_three)
            pass_power_3 = _three_consecutive(observed)
            triggers = set(capability.repeat_policy["triggers"])
            had_failure = any(
                packet.classification is ExecutionClassification.VERIFIED_FAILURE
                for packet in observed
            )
            unstable = len({packet.classification for packet in observed}) > 1
            required_consecutive = int(capability.repeat_policy["consecutive_passes"])
            if triggers & {"consequential", "unstable"} or had_failure or unstable:
                required_consecutive = max(required_consecutive, 3)

            if not required:
                status = "NOT_REQUIRED"
                reason = "catalog path is explicitly not required"
            elif any(
                packet.classification is ExecutionClassification.VERIFIED_FAILURE
                for packet in observed
            ) and not (
                required_consecutive == 3 and pass_power_3
            ):
                status = "VERIFIED_FAILURE"
                reason = "a verified failure has not been followed by strict 3/3 evidence"
            elif required_consecutive >= 3 and pass_power_3:
                status = "COMPLETE"
                reason = "three consecutive passes share one unchanged candidate identity"
            elif required_consecutive == 1 and passes:
                status = "COMPLETE"
                reason = "required path has deterministic passing evidence"
            elif any(
                packet.classification
                in {ExecutionClassification.PENDING, ExecutionClassification.ENVIRONMENT_BLOCKED}
                for packet in observed
            ):
                status = "PENDING"
                reason = "execution is pending or environment-blocked"
            elif observed:
                status = "PENDING"
                reason = f"repeat requirement not met: need {required_consecutive} consecutive pass(es)"
            else:
                status = "PENDING"
                reason = "no result packet exists for this required path"
            grades.append(
                PathGrade(
                    capability_id=capability.capability_id,
                    path=path,
                    required=required,
                    status=status,
                    observed_trials=len(observed),
                    required_consecutive=required_consecutive,
                    pass_at_3=pass_at_3,
                    pass_power_3=pass_power_3,
                    reason=reason,
                )
            )

    required_grades = [item for item in grades if item.required]
    failed = sum(item.status == "VERIFIED_FAILURE" for item in required_grades)
    pending = sum(item.status == "PENDING" for item in required_grades)
    passed = sum(item.status == "COMPLETE" for item in required_grades)
    if failed:
        status, exit_code = "VERIFIED_FAILURE", 1
    elif pending:
        status, exit_code = "PENDING", 75
    else:
        status, exit_code = "COMPLETE", 0
    source_coverage = Counter(item.source_pack for item in capabilities)
    return GradeReport(
        lane=lane,
        status=status,
        exit_code=exit_code,
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        candidate_hash=candidate_identity,
        source_coverage=dict(source_coverage),
        required_paths=len(required_grades),
        passed_paths=passed,
        pending_paths=pending,
        failed_paths=failed,
        pass_at_3_paths=sum(item.pass_at_3 for item in required_grades),
        pass_power_3_paths=sum(item.pass_power_3 for item in required_grades),
        path_grades=tuple(grades),
    )


__all__ = ["GradeReport", "PathGrade", "grade_packets"]

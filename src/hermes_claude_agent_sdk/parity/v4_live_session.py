"""Bounded provider-free normal-Hermes sessions over one live gateway."""
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from .v4_host_probe import (
    collect_v4_delegation_observation,
    collect_v4_host_observation,
)
from .v4_gateway import OpaqueHandle
from .v4_live_executor import (
    V4LiveExecutor,
    V4LiveExecutorViolation,
    V4LiveGateway,
    _candidate,
    _preflight_hash,
    _reject_raw,
    _safe_id,
)
from .v4_live_map import (
    TOTAL_CALL_COUNT,
    TURN_BUDGET,
    load_v4_live_execution_map,
    validate_v4_live_execution_map,
)

class V4LiveSessionViolation(V4LiveExecutorViolation):
    """A session could not be admitted, resumed, or closed safely."""

def _row_turn_budget(live_map: Mapping[str, Any], source_pack: str, source_item_id: str) -> int:
    rows = live_map.get("rows", ())
    row = next((item for item in rows if isinstance(item, Mapping) and item.get("source_pack") == source_pack and item.get("source_item_id") == source_item_id), None)
    if row is None or row.get("parent_calls") is None:
        raise V4LiveSessionViolation("row parent-call ledger is unavailable")
    calls, trials = row.get("parent_calls"), row.get("required_trial_indexes")
    if type(calls) is not int or not isinstance(trials, list) or not trials or calls % len(trials):
        raise V4LiveSessionViolation("row parent-call ledger is invalid")
    budget = calls // len(trials)
    if not 1 <= budget <= 4: raise V4LiveSessionViolation("row parent-turn budget is outside 1..4")
    return budget

class V4LiveSession:
    """Own one gateway lifecycle and a bounded sequence of independent turns."""

    def __init__(
        self,
        *,
        gateway: V4LiveGateway,
        candidate: Mapping[str, Any],
        preflight_projections: Mapping[str, Mapping[str, Any]],
        live_map: Mapping[str, Any] | str | Path,
        map_path: str | Path | None = None,
        expected_live_map_sha256: str | None = None,
        session_params: Mapping[str, Any] | None = None,
        planned_calls: int = 1,
        planned_turns: int = 1,
        resume_stored_session_id: str | None = None,
    ) -> None:
        if type(planned_calls) is not int or not 1 <= planned_calls <= TOTAL_CALL_COUNT:
            raise V4LiveSessionViolation("session call budget is invalid")
        if type(planned_turns) is not int or not 1 <= planned_turns <= TURN_BUDGET:
            raise V4LiveSessionViolation("session turn budget is invalid")
        params = dict(session_params or {})
        if set(params) - {"cols", "cwd", "hidden", "source", "title"}:
            raise V4LiveSessionViolation("session parameters contain unsupported fields")
        try:
            _reject_raw(params, "session_params")
        except V4LiveExecutorViolation as exc:
            raise V4LiveSessionViolation("session parameters contain forbidden data") from exc
        if resume_stored_session_id is not None:
            try:
                resume_stored_session_id = _safe_id(resume_stored_session_id, "stored_session_id")
            except V4LiveExecutorViolation as exc:
                raise V4LiveSessionViolation("stored session identity is invalid") from exc
        try:
            normalized_candidate, candidate_hash = _candidate(candidate)
            _preflight_hash(preflight_projections, candidate_hash)
            if isinstance(live_map, (str, Path)):
                live_map_path = Path(live_map).expanduser().resolve()
                live_map_document = load_v4_live_execution_map(live_map_path)
            else:
                live_map_document = dict(live_map)
                live_map_path = (
                    Path(map_path).expanduser().resolve()
                    if map_path is not None
                    else Path(__file__).resolve().parents[3]
                    / "qa"
                    / "parity-v4-live-execution-map.yaml"
                )
            accounting = validate_v4_live_execution_map(
                live_map_document, map_path=live_map_path
            )
            observed_map_hash = accounting.get("map_sha256")
            if (
                not isinstance(observed_map_hash, str)
                or expected_live_map_sha256 is not None
                and observed_map_hash != expected_live_map_sha256
            ):
                raise V4LiveSessionViolation("live map identity is invalid")
        except V4LiveSessionViolation:
            raise
        except Exception as exc:
            raise V4LiveSessionViolation(
                "candidate, preflight, or live-map admission failed"
            ) from exc
        self._gateway = gateway
        self._candidate = normalized_candidate
        self._preflights = {name: dict(value) for name, value in preflight_projections.items()}
        self._live_map, self._map_path = live_map_document, live_map_path
        self._expected_map_hash = observed_map_hash
        self._session_params = params
        self._planned_calls, self._planned_turns = planned_calls, planned_turns
        self._resume_stored_session_id = resume_stored_session_id
        self._live_session_id: str | None = None
        self._stored_session_id: str | None = None
        self._live_handle: OpaqueHandle | None = None
        self._stored_handle: OpaqueHandle | None = None
        self._turn_receipts: list[dict[str, Any]] = []
        self._row_turns: dict[tuple[str, str, int], int] = {}
        self._bound_row: tuple[str, str, int] | None = None
        self._provider_calls = 0
        self._started = False
        self._closed = False
        self._failed = False
    @property
    def started(self) -> bool:
        return self._started and not self._closed
    @property
    def live_handle(self) -> OpaqueHandle:
        if self._live_handle is None:
            raise V4LiveSessionViolation("session has not been started")
        return self._live_handle
    @property
    def stored_handle(self) -> OpaqueHandle:
        if self._stored_handle is None:
            raise V4LiveSessionViolation("session has not been started")
        return self._stored_handle
    def _capture_session_identities(self, raw: object, captured: dict[str, str]) -> None:
        if not isinstance(raw, Mapping):
            return
        for field in ("session_id", "stored_session_id"):
            value = raw.get(field)
            if isinstance(value, str) and value:
                captured[field] = value
    def start(self) -> dict[str, OpaqueHandle]:
        if self._started or self._closed or self._failed:
            raise V4LiveSessionViolation("session cannot be started twice")
        if self._resume_stored_session_id is not None:
            method, params = "session.resume", {"session_id": self._resume_stored_session_id, **self._session_params}
            params.setdefault("cols", 80)
        else:
            method, params = "session.create", dict(self._session_params)
        captured: dict[str, str] = {}
        try:
            self._gateway.start()
            response = self._gateway.call(
                method,
                params,
                projector=lambda raw: self._capture_session_identities(raw, captured),
            )
            live = captured.get("session_id") or response.get("session_id")
            stored = captured.get("stored_session_id") or response.get("stored_session_id")
            if not isinstance(live, str) or not live or not isinstance(stored, str) or not stored:
                raise V4LiveSessionViolation("gateway did not return both session identities")
            live, stored = _safe_id(live, "session_id"), _safe_id(stored, "stored_session_id")
            if self._resume_stored_session_id is not None and stored != self._resume_stored_session_id:
                raise V4LiveSessionViolation("resumed stored session identity does not match")
            self._live_session_id, self._stored_session_id = live, stored
            self._live_handle = OpaqueHandle.from_value("live_session", live)
            self._stored_handle = OpaqueHandle.from_value("stored_session", stored)
            self._started = True
            return {"live_handle": self.live_handle, "stored_handle": self.stored_handle}
        except Exception as exc:
            self._failed = True
            try:
                self._gateway.close()
            except Exception:
                pass
            if isinstance(exc, V4LiveSessionViolation):
                raise
            raise V4LiveSessionViolation("session start failed") from None
    create = start
    def run_turn(
        self,
        prompt: str,
        *,
        source_pack: str,
        source_item_id: str,
        path: str,
        trial_index: int,
        approval_choice: str = "deny",
        planned_calls: int = 1,
    ) -> dict[str, Any]:
        if not self.started or self._live_session_id is None:
            raise V4LiveSessionViolation("session is not live")
        if path != "positive":
            self._failed = True
            self.close()
            raise V4LiveSessionViolation(
                "provider turns are admitted only for the positive path"
            )
        if len(self._turn_receipts) >= self._planned_turns:
            self._failed = True
            self.close()
            raise V4LiveSessionViolation("session turn budget exhausted")
        remaining = self._planned_calls - self._provider_calls
        if type(planned_calls) is not int or not 1 <= planned_calls <= remaining:
            self._failed = True
            self.close()
            raise V4LiveSessionViolation("session provider-call budget exhausted")
        try:
            row_key = (source_pack, source_item_id, trial_index)
            row_budget = _row_turn_budget(self._live_map, source_pack, source_item_id)
            if self._bound_row is None:
                if self._planned_calls != row_budget or self._planned_turns != row_budget:
                    raise V4LiveSessionViolation(
                        "session budget does not equal the row trial budget"
                    )
                self._bound_row = row_key
            elif self._bound_row != row_key:
                raise V4LiveSessionViolation(
                    "a live session cannot cross row or trial boundaries"
                )
            if planned_calls != 1:
                raise V4LiveSessionViolation(
                    "one Hermes turn must submit exactly one parent provider call"
                )
            if self._row_turns.get(row_key, 0) >= row_budget:
                raise V4LiveSessionViolation("row parent-turn budget exhausted")
            executor = V4LiveExecutor(
                gateway=self._gateway,
                candidate=self._candidate,
                preflight_projections=self._preflights,
                live_map=self._live_map,
                map_path=self._map_path,
                expected_live_map_sha256=self._expected_map_hash,
                source_pack=source_pack,
                source_item_id=source_item_id,
                path=path,
                trial_index=trial_index,
                planned_calls=planned_calls,
                planned_turns=self._planned_turns,
                session_params=self._session_params,
            )
            receipt = executor.run_on_session(
                prompt,
                session_id=self._live_session_id,
                approval_choice=approval_choice,
            )
            used = receipt.get("provider_calls")
            if type(used) is not int or used < 1 or self._provider_calls + used > self._planned_calls:
                raise V4LiveSessionViolation("turn exceeded the session provider-call budget")
            result = dict(receipt)
            result["turn_index"] = len(self._turn_receipts) + 1
            self._turn_receipts.append(result)
            self._row_turns[row_key] = self._row_turns.get(row_key, 0) + 1
            self._provider_calls += used
            return result
        except Exception as exc:
            self._failed = True
            self.close()
            if isinstance(exc, V4LiveSessionViolation):
                raise
            raise V4LiveSessionViolation("session turn failed") from None
    turn = run_turn
    def close(self) -> None:
        if self._closed:
            return
        try:
            self._gateway.close()
        except Exception as exc:
            self._failed = True
            self._closed = True
            raise V4LiveSessionViolation("gateway close failed") from exc
        self._closed = True
    def collect_host_observation(
        self,
        db_path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        expected_turn_count: int,
    ) -> dict[str, Any]:
        """Collect the closed, sanitized host proof for this session."""
        if (
            not self._started
            or self._failed
            or not isinstance(self._stored_session_id, str)
            or not self._stored_session_id
        ):
            raise V4LiveSessionViolation("session is not valid for host observation")
        if type(expected_turn_count) is not int or not 1 <= expected_turn_count <= 4:
            raise V4LiveSessionViolation("host observation turn count is invalid")
        try:
            observation = collect_v4_host_observation(
                db_path,
                self._stored_session_id,
                allowed_root=allowed_root,
                expected_turn_count=expected_turn_count,
            )
            if not isinstance(observation, Mapping) or observation.get("status") != "PASS":
                raise V4LiveSessionViolation("host observation is not a closed PASS")
            return dict(observation)
        except Exception:
            self._failed = True
            if not self._closed:
                try:
                    self.close()
                except Exception:
                    pass
            raise V4LiveSessionViolation("host observation failed") from None
    def collect_delegation_observation(
        self,
        db_path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        """Collect durable delegation proof using only this session's stored ID."""
        if (
            not self._started
            or self._failed
            or not isinstance(self._stored_session_id, str)
            or not self._stored_session_id
        ):
            raise V4LiveSessionViolation("session is not valid for delegation observation")
        if expected_count is not None and (
            type(expected_count) is not int or not 0 <= expected_count <= 10_000
        ):
            raise V4LiveSessionViolation("delegation observation count is invalid")
        try:
            observation = collect_v4_delegation_observation(
                db_path,
                self._stored_session_id,
                allowed_root=allowed_root,
                expected_count=expected_count,
            )
            if not isinstance(observation, Mapping) or observation.get("status") != "PASS":
                raise V4LiveSessionViolation("delegation observation is not a closed PASS")
            return dict(observation)
        except Exception:
            self._failed = True
            if not self._closed:
                try:
                    self.close()
                except Exception:
                    pass
            raise V4LiveSessionViolation("delegation observation failed") from None
    def restart(self, *, gateway: V4LiveGateway, planned_calls: int | None = None, planned_turns: int | None = None) -> "V4LiveSession":
        if self._stored_session_id is None or not self._closed:
            raise V4LiveSessionViolation("session has no stored identity or is still live")
        resumed = type(self)(
            gateway=gateway,
            candidate=self._candidate,
            preflight_projections=self._preflights,
            live_map=self._live_map,
            map_path=self._map_path,
            expected_live_map_sha256=self._expected_map_hash,
            session_params=self._session_params,
            planned_calls=self._planned_calls if planned_calls is None else planned_calls,
            planned_turns=self._planned_turns if planned_turns is None else planned_turns,
            resume_stored_session_id=self._stored_session_id,
        )
        resumed._turn_receipts = [dict(item) for item in self._turn_receipts]
        resumed._row_turns = dict(self._row_turns)
        resumed._bound_row = self._bound_row
        resumed._provider_calls = self._provider_calls
        return resumed
    resume = restart

__all__ = ["V4LiveSession", "V4LiveSessionViolation"]

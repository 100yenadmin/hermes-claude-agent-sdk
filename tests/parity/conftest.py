from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.catalog import Catalog, load_catalog
from hermes_claude_agent_sdk.parity.results import ExecutionClassification, ResultPacket


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "qa" / "parity-contract-v3.yaml"


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_catalog(CATALOG_PATH)


@pytest.fixture()
def candidate_fields() -> dict[str, str]:
    return {
        "plugin_sha": "1" * 40,
        "host_sha": "2" * 40,
        "sdk_version": "0.2.144",
        "profile_id": "fable-v3-isolated",
        "profile_hash": "3" * 64,
        "runner_version": "3.0.0",
        "inventory_hash": "4" * 64,
    }


def make_packet(
    catalog: Catalog,
    capability_id: str,
    path: str,
    trial_index: int,
    candidate_fields: dict[str, str],
    *,
    classification: ExecutionClassification | None = None,
) -> ResultPacket:
    capability = catalog.by_id[capability_id]
    if classification is None:
        classification = (
            ExecutionClassification.EXPECTED_NEGATIVE
            if path == "denial"
            else ExecutionClassification.COMPLETE
        )
    outcome = {
        ExecutionClassification.COMPLETE: "completed",
        ExecutionClassification.EXPECTED_NEGATIVE: "denied",
        ExecutionClassification.VERIFIED_FAILURE: "failed",
    }.get(classification)
    events = ()
    if outcome is not None:
        events = (
            {"sequence": 1, "kind": "start", "status": "started"},
            {
                "sequence": 2,
                "kind": "terminal",
                "status": outcome,
                "terminal_outcome": outcome,
            },
        )
    passing = classification in {
        ExecutionClassification.COMPLETE,
        ExecutionClassification.EXPECTED_NEGATIVE,
    }
    return ResultPacket.build(
        capability_id=capability.capability_id,
        source_pack=capability.source_pack,
        lane=capability.lane,
        path=path,
        execution_id=capability.execution_id,
        classification=classification,
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        billing_classification="subscription_included" if passing else "none",
        turn_count=0,
        trial_index=trial_index,
        normalized_events=events,
        primary_proof_hash="a" * 64 if passing else None,
        secondary_proof_hash="b" * 64 if passing else None,
        reason_code=None if passing else "synthetic_failure",
        **candidate_fields,
    )

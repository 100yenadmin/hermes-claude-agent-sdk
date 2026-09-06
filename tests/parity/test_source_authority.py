from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from hermes_claude_agent_sdk.parity.source_authority import (
    SourceAuthorityViolation,
    validate_source_authority,
)


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def test_source_authority_binds_all_124_rc_rows_to_one_execution_catalog(catalog) -> None:
    report = validate_source_authority(catalog)

    assert dict(report.source_counts) == {
        "agent_sdk_boundary": 23,
        "clawprobench_native": 36,
        "openclaw_active": 12,
        "v2_non_soak": 53,
    }
    assert len(report.active_aliases) == 12
    assert set(report.active_aliases.values()) == {
        item.source_item_id
        for item in catalog.capabilities
        if item.source_pack == "openclaw_active"
    }
    assert report.boundary_status_counts == {
        "covered_current": 9,
        "equivalent_host": 9,
        "not_runtime_applicable": 5,
    }
    assert len(report.authority_hash) == 64
    assert report.to_dict()["requires_0_3_239_rows"] == []


def test_source_authority_rejects_sdk_status_drift(catalog) -> None:
    capabilities = list(catalog.capabilities)
    index = next(
        index
        for index, item in enumerate(capabilities)
        if item.source_pack == "agent_sdk_boundary"
    )
    capabilities[index] = replace(
        capabilities[index], sdk_ledger_status="requires_0_3_239"
    )
    mutated = replace(catalog, capabilities=tuple(capabilities))
    with pytest.raises(SourceAuthorityViolation, match="status drift"):
        validate_source_authority(mutated)


def test_source_authority_rejects_unexcluded_preliminary_input(catalog) -> None:
    contract = _thaw(catalog.contract)
    authority = contract["source_authority"]
    authority["excluded_preliminary_inputs"] = authority["excluded_preliminary_inputs"][:-1]
    mutated = replace(catalog, contract=contract)
    with pytest.raises(SourceAuthorityViolation, match="explicitly excluded"):
        validate_source_authority(mutated)

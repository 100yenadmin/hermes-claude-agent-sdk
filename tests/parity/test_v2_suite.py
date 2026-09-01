from __future__ import annotations

from hermes_claude_agent_sdk.parity.v2_suite import _V2_NODES, v2_execution_ids


def test_every_v2_non_soak_row_has_one_exact_mapping(catalog) -> None:
    source_ids = {
        item.capability_id
        for item in catalog.capabilities
        if item.source_pack == "v2_non_soak"
    }
    assert len(source_ids) == 53
    assert set(_V2_NODES) == source_ids
    assert len(v2_execution_ids()) == 53
    assert len(set(v2_execution_ids())) == 53


def test_v2_mapping_never_names_forbidden_live_surfaces() -> None:
    rendered = repr(_V2_NODES).lower()
    assert "telegram" not in rendered
    assert "customer" not in rendered
    assert "shared-eva" not in rendered

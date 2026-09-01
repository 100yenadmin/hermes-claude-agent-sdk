"""Feature-first parity contracts, canonical primitives, and grading."""

from .canonical import (
    CanonicalizationError,
    SDK_EVENT_CODES,
    TRACE_REGISTRY,
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    canonicalize,
    load_json,
    stable_json_hash,
    validate_identifier,
    validate_sha256,
)
from .catalog import Catalog, CatalogViolation, load_catalog
from .grader import GradeReport, grade_packets
from .inventory import InventoryViolation, ToolInventory, load_tool_inventory
from .profile import ProfileManifest, ProfileViolation, load_profile_manifest
from .results import ExecutionClassification, ResultPacket, ResultViolation
from .runner import (
    ExecutionBundle,
    ExecutionContext,
    ExecutionOutcome,
    ExecutorRegistry,
    run_catalog,
    validate_run_manifest,
)

__all__ = [
    "Catalog",
    "CatalogViolation",
    "CanonicalizationError",
    "ExecutionBundle",
    "ExecutionClassification",
    "ExecutionContext",
    "ExecutionOutcome",
    "ExecutorRegistry",
    "GradeReport",
    "InventoryViolation",
    "ProfileManifest",
    "ProfileViolation",
    "ResultPacket",
    "ResultViolation",
    "SDK_EVENT_CODES",
    "TRACE_REGISTRY",
    "ToolInventory",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_sha256",
    "canonicalize",
    "grade_packets",
    "load_catalog",
    "load_json",
    "load_profile_manifest",
    "load_tool_inventory",
    "run_catalog",
    "stable_json_hash",
    "validate_identifier",
    "validate_run_manifest",
    "validate_sha256",
]

"""Feature-first parity contracts, sanitized results, and deterministic grading."""

from .catalog import Catalog, CatalogViolation, load_catalog
from .grader import GradeReport, grade_packets
from .inventory import InventoryViolation, ToolInventory, load_tool_inventory
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
    "ExecutionClassification",
    "ExecutionBundle",
    "ExecutionContext",
    "ExecutionOutcome",
    "ExecutorRegistry",
    "GradeReport",
    "InventoryViolation",
    "ResultPacket",
    "ResultViolation",
    "ToolInventory",
    "grade_packets",
    "load_catalog",
    "load_tool_inventory",
    "run_catalog",
    "validate_run_manifest",
]

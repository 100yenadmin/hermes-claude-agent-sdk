"""Run the approval-followthrough proof from an installed wheel."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path


def _fail(reason: str = "installed approval-followthrough proof failed") -> None:
    raise SystemExit(reason)


def main() -> None:
    host_root_value = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not host_root_value:
        _fail("HERMES_AGENT_HOST_ROOT is not configured")
    host_root = Path(host_root_value)
    if not host_root.is_dir():
        _fail("HERMES_AGENT_HOST_ROOT is not a directory")

    entries = importlib.metadata.entry_points(
        group="hermes_agent.plugins", name="claude-agent-sdk"
    )
    if len(entries) != 1:
        _fail()
    plugin = next(iter(entries)).load()
    plugin_file = Path(plugin.__file__).resolve()
    site_prefixes = {
        (Path(sys.prefix) / "lib").resolve(),
        (Path(sys.prefix) / "Lib").resolve(),
    }
    if not any(prefix in plugin_file.parents for prefix in site_prefixes):
        _fail()

    from hermes_claude_agent_sdk.parity.approval_followthrough import (
        render_report,
        run_approval_followthrough,
    )

    report = run_approval_followthrough(
        host_root=str(host_root),
        plugin_module=plugin,
    )
    if report.get("status") != "passed":
        _fail()
    print(render_report(report))


if __name__ == "__main__":
    main()

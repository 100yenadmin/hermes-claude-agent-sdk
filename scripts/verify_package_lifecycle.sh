#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
if command -v python >/dev/null 2>&1; then
    python_bin="python"
else
    python_bin="python3"
fi

cd "$repo_root"
printf 'verified package lifecycle repository: '
pwd

if ! "$python_bin" -c 'import build' >/dev/null 2>&1; then
    echo "python build module is required; install the repository test dependencies first" >&2
    exit 1
fi
if ! "$python_bin" -c 'import pytest' >/dev/null 2>&1; then
    echo "pytest is required; install the repository test dependencies first" >&2
    exit 1
fi

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/hermes-claude-agent-sdk-lifecycle.XXXXXX")"
trap 'rm -rf -- "$work_dir"' EXIT
dist_dir="$work_dir/dist"
home_dir="$work_dir/home"
hermes_home="$work_dir/hermes-home"
config_dir="$work_dir/config"
cache_dir="$work_dir/cache"
mkdir -p "$dist_dir" "$home_dir" "$hermes_home" "$config_dir" "$cache_dir"
export HOME="$home_dir"
export HERMES_HOME="$hermes_home"
export XDG_CONFIG_HOME="$config_dir"
export XDG_CACHE_HOME="$cache_dir"

# Build locally from the checked-out source.  --no-isolation avoids a second
# package-index resolution phase; the CI job supplies the pinned build tools.
"$python_bin" -m build \
    --sdist \
    --wheel \
    --no-isolation \
    --outdir "$dist_dir" \
    "$repo_root"

wheel="$(find "$dist_dir" -maxdepth 1 -type f -name '*.whl' -print -quit)"
sdist="$(find "$dist_dir" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)"
if [[ -z "$wheel" || -z "$sdist" ]]; then
    echo "build did not produce exactly the required wheel and sdist artifacts" >&2
    exit 1
fi

"$python_bin" - "$wheel" "$sdist" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"artifact sha256 {digest}  {path.name}")
PY

HERMES_LIFECYCLE_WHEEL="$wheel" \
HERMES_LIFECYCLE_SDIST="$sdist" \
    "$python_bin" -m pytest -q -s "$repo_root/tests/test_package_lifecycle.py"

echo "package wheel/sdist lifecycle passed without network installation"

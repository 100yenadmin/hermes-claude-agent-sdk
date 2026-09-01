#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
python_bin="${HERMES_PARITY_TEST_PYTHON:-$repo_root/.venv/bin/python}"

if [ ! -x "$python_bin" ]; then
  echo "error: test Python is unavailable: $python_bin" >&2
  exit 127
fi

cd "$repo_root"
exec env \
  -u ANTHROPIC_API_KEY \
  -u ANTHROPIC_AUTH_TOKEN \
  -u ANTHROPIC_BASE_URL \
  -u CLAUDE_API_KEY \
  -u CLAUDE_CODE_USE_BEDROCK \
  -u CLAUDE_CODE_USE_VERTEX \
  TZ=UTC \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8 \
  PYTHONHASHSEED=0 \
  "$python_bin" -m pytest "$@"

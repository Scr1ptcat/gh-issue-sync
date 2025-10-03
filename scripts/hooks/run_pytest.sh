#!/usr/bin/env bash
set -uo pipefail

MARKER="${1:-unit}"

# Use repo venv if present; otherwise rely on system python
PYTHON_BIN="python3"
command -v ${PYTHON_BIN} >/dev/null 2>&1 || PYTHON_BIN="python"

# Ensure venv exists
if [[ ! -f ".venv/pyvenv.cfg" ]]; then
  ${PYTHON_BIN} -m venv .venv
fi

# Activate venv
# shellcheck disable=SC1091
source .venv/bin/activate

# Install dev dependencies once
if [[ ! -f ".venv/.precommit_deps_installed" ]]; then
  python -m pip install -U pip setuptools wheel >/dev/null
  # Install project with dev extras
  python -m pip install -e '.[dev]' >/dev/null
  touch .venv/.precommit_deps_installed
fi

# Run pytest limited to the requested marker. If no tests are collected (exit 5), treat as success.
rc=0
pytest -m "${MARKER}" -q || rc=$?
if [[ ${rc} -eq 5 ]]; then
  # No tests collected for this marker -> success
  exit 0
fi
exit ${rc}


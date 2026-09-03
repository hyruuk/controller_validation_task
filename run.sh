#!/usr/bin/env bash
# Launch the controller validation task.
#
# Usage:
#   bash run.sh                     # config wizard (first run) + subject picker
#   bash run.sh 01 001              # subject 01, session 001
#   bash run.sh 01 001 --no-fullscreen --sync-mode none
#
# Every argument is forwarded verbatim to `python -m controller_validation_task`.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
PY="${VENV_DIR}/bin/python"

if [[ ! -x "${PY}" ]]; then
  echo "venv not found at ${VENV_DIR}; run 'bash setup_env.sh' first." >&2
  exit 1
fi

# Run from the repo root so a relative config.json / output/ resolves the way
# the operator expects, regardless of where they invoked the script from.
cd "${ROOT_DIR}"
exec "${PY}" -m controller_validation_task "$@"

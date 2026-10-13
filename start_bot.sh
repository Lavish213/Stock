#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${LAVISH_VENV_PATH:-$SCRIPT_DIR/venv}"
PYTHON="$VENV_DIR/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "Virtual environment missing. Create $VENV_DIR and install requirements-deploy.txt."
    exit 1
fi
cd "$SCRIPT_DIR"
if ! "$PYTHON" "$SCRIPT_DIR/system_boot.py"; then
    echo "Pre-flight failed; bot was not started."
    exit 1
fi
exec "$PYTHON" "$SCRIPT_DIR/run_ingest.py"

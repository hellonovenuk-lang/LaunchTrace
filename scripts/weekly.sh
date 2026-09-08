#!/usr/bin/env bash
# Run the weekly pipeline and show the result. Never sends: approve separately.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
python -m src.pipeline weekly "$@"
echo
echo "Nothing has been sent. To deliver this run:"
echo "  python -m src.pipeline status"
echo "  python -m src.pipeline approve --run-id <run id>"
echo "  python -m src.pipeline send --run-id <run id>"

#!/usr/bin/env bash
# Start the LaunchTrace website locally at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
python -m src.pipeline init-db
exec python -m uvicorn src.web.app:app --reload --host 127.0.0.1 --port 8000

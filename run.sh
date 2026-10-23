#!/usr/bin/env bash
# Start CarbonOps. The app starts its own AWS emulator (moto) on AWS_ENDPOINT_URL (default localhost:5050).
set -euo pipefail
cd "$(dirname "$0")"
unset VIRTUAL_ENV   # an activated venv from another project would make uv print a warning
exec uv run streamlit run app.py "$@"

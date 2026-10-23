#!/usr/bin/env bash
# Start CarbonOps. The app starts its own AWS emulator (moto) on AWS_ENDPOINT_URL (default localhost:5050).
set -euo pipefail
cd "$(dirname "$0")"
exec uv run streamlit run app.py "$@"

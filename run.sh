#!/usr/bin/env bash
# Start moto_server (fake AWS) in the background, then the Streamlit app. Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

uv run moto_server -p 5050 > /dev/null 2>&1 &
MOTO_PID=$!
trap 'kill "$MOTO_PID" 2>/dev/null || true' EXIT

uv run streamlit run app.py

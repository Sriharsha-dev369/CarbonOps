#!/usr/bin/env bash
# Pre-recording check: fresh estates, both clients end to end (rules and replayed LLM), full tests,
# and no API keys in git history. Prints PASS or FAIL. Usage: ./scripts/demo_check.sh
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
step() { printf '%-64s' "$1"; }
ok() { echo "ok"; }
bad() { echo "FAIL"; echo "$1" | sed 's/^/    /' | tail -15; fail=1; }
expect() {  # expect <label> <output> <pattern>...
  local label=$1 out=$2; shift 2
  for pattern in "$@"; do
    grep -qF -- "$pattern" <<<"$out" || { step "$label"; bad "missing: $pattern"$'\n'"$out"; return; }
  done
  step "$label"; ok
}

for client in client_a client_b; do
  out=$(uv run python -m carbonops.cli reset --client "$client" 2>&1) || { step "reset $client"; bad "$out"; continue; }
  step "reset $client"; ok
done

out=$(uv run python -m carbonops.cli run --client client_a --approve-all 2>&1)
expect "Acme Retail: 3 auto · 5 approval · 2 blocked · 1 none" "$out" \
  "AUTO 3 · APPROVE 5 · BLOCK 2 · no finding 1" "ROLLED_BACK" "Realised: ₹96,166/month"
out=$(uv run python -m carbonops.cli run --client client_b --approve-all 2>&1)
expect "Northbank Finance: 1 auto · 7 approval · 2 blocked · 1 none" "$out" \
  "AUTO 1 · APPROVE 7 · BLOCK 2 · no finding 1"

for client in client_a client_b; do
  # Fresh estate again: recordings match only the seeded estate (and the app may share this AWS emulator).
  uv run python -m carbonops.cli reset --client "$client" >/dev/null 2>&1
  out=$(REPLAY_MODE=play LLM_API_KEY= uv run python -m carbonops.cli run --client "$client" --approve-all --llm 2>&1)
  expect "$client with replayed LLM (offline)" "$out" "LLM proposed 9 of 9 actions" "Report written by the LLM"
done

out=$(uv run pytest -q 2>&1)
if [[ $? -eq 0 ]]; then step "pytest: $(tail -1 <<<"$out")"; ok; else step "pytest"; bad "$out"; fi

if git log -p | grep -qi "gsk_"; then step "no Groq keys in git history"; bad "found gsk_ in history"; else step "no Groq keys in git history"; ok; fi

echo
if [[ $fail -eq 0 ]]; then echo "PASS"; else echo "FAIL"; fi
exit $fail

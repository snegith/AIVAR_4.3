#!/usr/bin/env bash
#
# post_deploy_smoke.sh — the FIRST validation to run after every deploy,
# before touching the Langfuse UI, demo prep, or anything else.
#
# Why (Phase 8 -> Phase 11 carry-forward): Persona C (normal high-volume user)
# is the false-positive risk on AWS/RDS. Remote DB latency and BackgroundTask
# timing can shift risk scores relative to CI, so the calibration MUST be
# re-verified against the live RDS-backed API. If Persona C scores at or above
# WATCH_THRESHOLD (45.0) on RDS, the deploy is NOT demo-ready — stop and tune
# the config-driven thresholds/weights (no detector logic changes).
#
# This runs `simulate.py` inside the already-running api container via
# `docker compose exec`, so the only host dependency is Docker. Two gates:
#   1. simulate.py's own exit code (every persona criterion, incl. C < 45).
#   2. An explicit, dedicated Persona C margin check with a crisp message,
#      parsed from the machine-readable --json-summary (not the text table).
#
# Usage:  ./deploy/post_deploy_smoke.sh
# Env:    COMPOSE_FILE, SMOKE_SEED, SMOKE_DELAY_MS, SMOKE_TARGET_URL,
#         SMOKE_READY_ATTEMPTS

set -uo pipefail

log() { printf '[smoke] %s\n' "$*"; }
die() { printf '[smoke][error] %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.ec2.yml}"
SMOKE_SEED="${SMOKE_SEED:-42}"
SMOKE_DELAY_MS="${SMOKE_DELAY_MS:-500}"
SMOKE_TARGET_URL="${SMOKE_TARGET_URL:-http://127.0.0.1:8000}"
SMOKE_READY_ATTEMPTS="${SMOKE_READY_ATTEMPTS:-60}"
SUMMARY_PATH="/tmp/ps43_smoke_summary.json"

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# ---------------------------------------------------------------------------
# 1. Block until the live RDS-backed API is actually healthy.
# ---------------------------------------------------------------------------
log "Waiting for API readiness at ${SMOKE_TARGET_URL}/ready (in-container) ..."
ready=0
for attempt in $(seq 1 "$SMOKE_READY_ATTEMPTS"); do
  if dc exec -T api python -c "
import sys, urllib.request
try:
    r = urllib.request.urlopen('${SMOKE_TARGET_URL}/ready', timeout=5)
    sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    log "API ready after ${attempt} attempt(s)."
    ready=1
    break
  fi
  sleep 3
done
[[ "$ready" -eq 1 ]] || die "API never became ready — refusing to run the smoke gate (deploy is unhealthy)."

# ---------------------------------------------------------------------------
# 2. Run the four-persona harness against the live API (Gate 1: exit code).
# ---------------------------------------------------------------------------
log "Running simulate.py against the live RDS-backed API (seed=${SMOKE_SEED}, delay=${SMOKE_DELAY_MS}ms) ..."
dc exec -T api python simulate.py \
  --base-url "$SMOKE_TARGET_URL" \
  --seed "$SMOKE_SEED" \
  --request-delay-ms "$SMOKE_DELAY_MS" \
  --json-summary "$SUMMARY_PATH"
simulate_rc=$?

if [[ "$simulate_rc" -ne 0 ]]; then
  die "simulate.py FAILED (exit ${simulate_rc}). At least one persona criterion did not pass on RDS. Do NOT proceed to the demo."
fi
log "simulate.py passed all persona criteria (Gate 1 OK)."

# ---------------------------------------------------------------------------
# 3. Explicit, dedicated Persona C false-positive gate (Gate 2).
# ---------------------------------------------------------------------------
log "Verifying Persona C margin against WATCH_THRESHOLD on RDS ..."
dc exec -T api python -c "
import json, sys
with open('${SUMMARY_PATH}', encoding='utf-8') as fh:
    s = json.load(fh)
watch = float(s['thresholds']['watch_threshold'])
scores = s['personas']
c = float(scores['C_normal_user'])
print('  --- Persona risk scores on RDS ---')
for name in sorted(scores):
    print(f'    {name:<24} {scores[name]:>7.2f}')
print(f'  WATCH_THRESHOLD = {watch:.2f}')
print(f'  Persona C margin below threshold = {watch - c:.2f}')
if c >= watch:
    print(f'  PERSONA C GATE: FAIL — C={c:.2f} >= WATCH_THRESHOLD={watch:.2f}')
    print('  Action: tune config-driven thresholds/weights (no detector logic) and redeploy.')
    sys.exit(2)
print(f'  PERSONA C GATE: PASS — C={c:.2f} < WATCH_THRESHOLD={watch:.2f}')
"
persona_c_rc=$?

if [[ "$persona_c_rc" -ne 0 ]]; then
  die "Persona C false-positive gate FAILED on RDS. Deploy is not demo-ready."
fi

log "Post-deploy smoke gate PASSED. Safe to open the Langfuse UI and start the demo."

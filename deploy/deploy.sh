#!/usr/bin/env bash
#
# deploy.sh — build, migrate, start, and validate the detector stack on EC2.
#
# Order matters and is deliberate:
#   1. Build the api image (bakes in the fastembed model + simulate harness).
#   2. Create the RDS databases and run Alembic BEFORE starting the API, so the
#      API never crash-loops against a missing detector_db.
#   3. Start api + Langfuse v2 (both -> RDS).
#   4. Run the post-deploy smoke gate as the FIRST validation after deploy
#      (simulate.py against live RDS + the explicit Persona C < 45 gate).
#      Nothing else — Langfuse UI, demo — happens until this passes.
#
# Run from the repo root on the EC2 box after ec2_bootstrap.sh has written .env.
#
# Usage:  ./deploy/deploy.sh

set -euo pipefail

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][error] %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.ec2.yml}"
[[ -f .env ]] || die ".env not found. Run ./deploy/ec2_bootstrap.sh first."
command -v docker >/dev/null 2>&1 || die "docker is not installed. Run ec2_bootstrap.sh first."
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing. Run ec2_bootstrap.sh first."

log "1/4 Building api image ..."
docker compose -f "$COMPOSE_FILE" build

log "2/4 Preparing RDS databases + running migrations ..."
bash "${SCRIPT_DIR}/migrate.sh"

log "3/4 Starting api + Langfuse v2 ..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

log "4/4 Running post-deploy smoke gate (first validation) ..."
bash "${SCRIPT_DIR}/post_deploy_smoke.sh"

# Surface the two public URLs the evaluator needs.
PUBLIC_HOST="$(grep -E '^LANGFUSE_HOST=' .env | head -n1 | cut -d= -f2- | sed -E 's#^https?://##; s#:[0-9]+$##')"
log "Deploy complete and validated."
if [[ -n "$PUBLIC_HOST" ]]; then
  log "  Detector API : http://${PUBLIC_HOST}:8000/docs"
  log "  Langfuse UI  : http://${PUBLIC_HOST}:3000"
fi
log "Reminder: run simulate.py BEFORE opening the Langfuse UI to keep peak memory under the ceiling."

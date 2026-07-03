#!/usr/bin/env bash
#
# migrate.sh — prepare the RDS databases for the detector stack.
#
# Why this exists: on RDS there is no local Postgres init script, so the two
# logical databases must be created explicitly. This script:
#   1. Creates `detector_db` and `langfuse_db` if they do not already exist
#      (idempotent, using the RDS master creds against the default `postgres` db).
#   2. Runs `alembic upgrade head` against `detector_db` INSIDE the api container
#      — migration 0001 runs `CREATE EXTENSION IF NOT EXISTS vector;` and creates
#      all tables. Langfuse v2 auto-migrates its own schema in `langfuse_db` on
#      first boot, so we only need the empty database to exist.
#
# Run from the repo root (or anywhere; it cd's to the repo root) after .env
# exists. Safe to re-run.

set -euo pipefail

log() { printf '[migrate] %s\n' "$*"; }
die() { printf '[migrate][error] %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.ec2.yml}"
[[ -f .env ]] || die ".env not found in ${REPO_ROOT}. Run ec2_bootstrap.sh first."

# Pull DATABASE_URL out of .env without sourcing the whole file.
DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | head -n1 | cut -d= -f2-)"
[[ -n "$DATABASE_URL" ]] || die "DATABASE_URL missing from .env"

# Admin URL: same creds/host, but the always-present default `postgres` db.
ADMIN_URL="${DATABASE_URL%/*}/postgres"

create_db_if_absent() {
  local dbname="$1"
  log "Ensuring database '${dbname}' exists ..."
  docker run --rm postgres:16 psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -tAc \
    "SELECT 'CREATE DATABASE ${dbname}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='${dbname}')" \
    | grep -q 'CREATE DATABASE' \
    && docker run --rm postgres:16 psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${dbname}" \
    || log "Database '${dbname}' already present."
}

create_db_if_absent detector_db
create_db_if_absent langfuse_db

log "Running Alembic migrations against detector_db (enables pgvector) ..."
docker compose -f "$COMPOSE_FILE" run --rm --no-deps api alembic upgrade head

log "Migrations complete."

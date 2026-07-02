# Adversarial Pattern Detector (PS-4.3)

Cross-session adversarial pattern detector: correlates probing, privilege
escalation, and enumeration signals across sessions per user.

## Local development (Phase 0)

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
```

Postgres is exposed on host port **5433** (not 5432) to avoid conflicts with a
local PostgreSQL install. Run migrations with `make migrate` or `alembic upgrade head`.

Install Python deps and run tests:

```bash
pip install -e ".[dev]"
pytest
```

## Architecture

See `PS-4.3-Implementation-Plan.md` for the full design. Source of truth is
`detector_db` on PostgreSQL with pgvector. Langfuse v2 is an optional trace
mirror (Phase 9).

## Langfuse v2 production caveat

Langfuse v2 is pinned intentionally for the free-tier self-hosted demo. For
production, migrate observability to Langfuse v3 on appropriately sized
infrastructure; the detector is unaffected because Langfuse is mirror-only.

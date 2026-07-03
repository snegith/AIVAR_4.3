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

## Simulation harness (Phase 8)

With the API running (`docker compose up -d`), drive all four personas and
assert risk/alert outcomes:

```bash
make simulate
# or: python simulate.py --dry-run --seed 42
```

Flags: `--base-url` (default `http://localhost:8000`), `--dry-run` (stub LLM),
`--request-delay-ms` (default **500** ms between requests).

At the default 500 ms delay, a full run takes approximately **75 seconds**
(~160 cross-session requests plus admin recompute and inactivity-reset checks).
The harness prints a pass/fail table for every persona criterion and exits
non-zero on any failure (CI gate).

## Langfuse v2 mirror (Phase 9)

Langfuse is **optional** and **mirror-only** — `detector_db` remains the source
of truth. With `LANGFUSE_ENABLED=false` (default, CI, `simulate.py`), mirroring
is a no-op.

To run Langfuse locally alongside the API:

```bash
make langfuse-up
# or: docker compose --profile langfuse up -d --build
```

Then enable mirroring in `.env` (or export env vars):

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-placeholder
LANGFUSE_SECRET_KEY=sk-lf-placeholder
```

Open the UI at **http://localhost:3000**. Each `POST /v1/events` stores a
`langfuse_trace_id` on the interaction row when mirroring succeeds.

**Docker note:** the API container uses `LANGFUSE_INGEST_HOST=http://langfuse:3000`
to reach Langfuse on the compose network; browser links still use
`LANGFUSE_HOST=http://localhost:3000`.

**Production caveat:** Langfuse v2 is pinned for the free-tier self-hosted demo.
For production, migrate observability to Langfuse v3 on larger infrastructure;
the detector is unaffected because Langfuse is mirror-only.

## Architecture

See `PS-4.3-Implementation-Plan.md` for the full design. Source of truth is
`detector_db` on PostgreSQL with pgvector. Langfuse v2 is an optional trace
mirror (see **Langfuse v2 mirror** above).

## Langfuse v2 production caveat (summary)

Langfuse v2 is pinned intentionally for the free-tier self-hosted demo. For
production, migrate observability to Langfuse v3 on appropriately sized
infrastructure; the detector is unaffected because Langfuse is mirror-only.

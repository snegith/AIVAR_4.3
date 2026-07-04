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

## CI (Phase 10)

GitHub Actions runs on every push/PR to `main`:

- `ruff check` on `app/`, `sim/`, `simulate.py`, `tests/`
- `mypy app`
- `alembic upgrade head` against Postgres (pgvector)
- `pytest` and `simulate.py --dry-run` with `LANGFUSE_ENABLED=false`

Locally:

```bash
pip install -e ".[dev]"
ruff check app sim simulate.py tests
mypy app
pytest
```

On Windows without `make`, use the commands above directly.

The `simulate.py` step runs against a live uvicorn started as a background
process and health-gated on `/ready` (before pytest and again immediately
before the harness). In CI, `test_simulate.py` **fails** (never skips) if the
API is unreachable, so a broken end-to-end run can't slip through green.

## LLM provider (Groq free tier)

The detector uses **Groq** (free tier, no credit card) as the real LLM backend.
CI and `simulate.py --dry-run` always use the deterministic stub via
`LLM_DRY_RUN=true` — unaffected by provider keys.

| Env var | Purpose |
|---------|---------|
| `LLM_PROVIDER` | `auto` (default), `groq`, or `stub` |
| `GROQ_API_KEY` | API key from [console.groq.com/keys](https://console.groq.com/keys) |
| `GROQ_MODEL` | Default `llama-3.1-8b-instant` (high free-tier quota) |

With `LLM_PROVIDER=auto`, **Groq is used** when `GROQ_API_KEY` is set, otherwise
the detector falls back to the deterministic stub.

**Groq rate limits:** the free tier is ~30 requests/minute. A full `simulate.py`
run issues ~225–245 LLM calls, so use **`--request-delay-ms 3000`** (or set
`SIMULATE_REQUEST_DELAY_MS=3000` in `.env`). EC2 bootstrap sets this
automatically. The Groq provider retries on 429 with exponential backoff.

```bash
# Local with Groq (API must not have LLM_DRY_RUN=true)
GROQ_API_KEY=gsk_... LLM_PROVIDER=groq docker compose up -d --build
python simulate.py --base-url http://localhost:8000 --request-delay-ms 3000
```

## AWS deployment (Phase 11)

Free-tier topology: one **EC2 t3.micro** runs the detector `api` (`:8000`) and
self-hosted **Langfuse v2** (`:3000`) via `docker-compose.ec2.yml`; one **RDS
db.t3.micro** hosts both `detector_db` (pgvector, source of truth) and
`langfuse_db`. No Postgres runs on the box.

### One-time provisioning

```bash
# On the EC2 box (Amazon Linux 2023):
REPO_URL=https://github.com/<you>/<repo>.git ./deploy/ec2_bootstrap.sh
```

`ec2_bootstrap.sh` installs Docker + the Compose plugin, creates a **2GB swap**
file, sets `vm.swappiness=10`, clones the repo, and writes a chmod-600 `.env`
(prompting for the **Groq API key** (free tier), RDS creds, admin key, and
Langfuse init keys — so first boot needs **no** manual UI step).

### Deploy

```bash
make deploy          # or: bash deploy/deploy.sh
```

`deploy.sh` builds the image, creates the RDS databases and runs Alembic
(`deploy/migrate.sh`, which enables pgvector), starts `api` + Langfuse v2, then
runs the **post-deploy smoke gate** (`deploy/post_deploy_smoke.sh`).

### Post-deploy smoke gate — Persona C on RDS (mandatory, runs first)

Immediately after every deploy — **before** opening the Langfuse UI or starting
the demo — `post_deploy_smoke.sh` runs `simulate.py` against the live
RDS-backed API and enforces two gates:

1. **simulate.py exit code** — every persona criterion, including Persona C
   staying below `WATCH_THRESHOLD` (45.0).
2. **Explicit Persona C margin check** — parsed from `simulate.py --json-summary`
   (not the text table). Persona C (normal high-volume user) is the
   false-positive risk on AWS: remote RDS latency and BackgroundTask timing can
   shift risk scores versus CI. **If Persona C scores ≥ 45.0 on RDS, the deploy
   is not demo-ready** — stop and tune only the config-driven
   thresholds/weights (no detector logic changes), then redeploy.

```bash
make smoke           # re-run the gate on demand
```

### Demo (two links from one AWS box)

1. Detector API docs at `http://EC2_IP:8000/docs`; Langfuse UI at
   `http://EC2_IP:3000`.
2. Run `python simulate.py --base-url http://EC2_IP:8000` and watch traces stream
   into Langfuse. **Run simulate before opening the UI** to keep peak memory
   under the 1GB ceiling.
3. Open `GET /v1/users/{prober}/patterns` and `/v1/alerts` to show the
   cross-session threat card.

## Architecture

See `PS-4.3-Implementation-Plan.md` for the full design. Source of truth is
`detector_db` on PostgreSQL with pgvector. Langfuse v2 is an optional trace
mirror (see **Langfuse v2 mirror** above).

## Langfuse v2 production caveat (summary)

Langfuse v2 is pinned intentionally for the free-tier self-hosted demo. For
production, migrate observability to Langfuse v3 on appropriately sized
infrastructure; the detector is unaffected because Langfuse is mirror-only.

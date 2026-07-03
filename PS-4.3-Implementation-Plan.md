# Cross-Session Adversarial Pattern Detector (PS-4.3) - Implementation Plan (v3: free-tier + self-hosted Langfuse v2)

Finalized plan for the implementation agent. This revision supersedes the earlier ones. Net design:
- Free-tier AWS: EC2 t3.micro (Docker Compose) + RDS db.t3.micro. No Fargate, ElastiCache, or ALB.
- Detection runs via FastAPI BackgroundTasks + Postgres advisory locks. No Celery, no Redis.
- Embeddings via fastembed (ONNX bge-small), not torch, to fit in 1GB RAM.
- Probing DBSCAN eps=0.25; escalation primary metric = Spearman/OLS slope (Mann-Kendall demoted).
- Admin endpoint drives the inactivity-reset test over HTTP; slowapi rate limiting on ingestion.
- Observability + demo UI: SELF-HOSTED, PINNED LANGFUSE v2 running on the SAME EC2 box, behind LANGFUSE_ENABLED. Our own `interactions` table stays the source of truth; Langfuse is a mirror only.

Why Langfuse v2 (not v3): v3 requires ClickHouse + Redis + blob storage + a worker container, which will not fit on a 1GB free-tier box. v2 is a lightweight two-piece system (Langfuse server + a Postgres database) that runs comfortably next to the detector and points at RDS. This gives a real, self-hosted, publicly reachable UI with zero paid infrastructure and no Langfuse Cloud.

Production caveat (state this in the README and demo): Langfuse v2 is pinned intentionally for this tech challenge to keep the self-hosted demo free-tier compatible; v2 receives no security patches after Q1 2025. For real production, migrate the observability layer to Langfuse v3 on appropriately sized infrastructure. The detector is unaffected by that migration because Langfuse is only a trace mirror, never the source of truth.

No thresholds are left as "TBD"; all are concrete, config-driven defaults calibrated by simulate.py.

---

## Guiding decisions (read first)

- Source of truth for detection is OUR `interactions` table in `detector_db` (Postgres). Langfuse is an OPTIONAL mirror for observability/UI only. Detectors, `simulate.py`, and CI never depend on Langfuse being up.
- Both the detector API and self-hosted Langfuse v2 deploy to the SAME free-tier EC2 and are both publicly reachable, giving the evaluator two links from one AWS machine.
- One RDS instance hosts two logical databases: `detector_db` (ours) and `langfuse_db` (Langfuse's own store). They never share tables.
- Real LLM (Anthropic Claude) is the default in every real run. A `--dry-run` local stub exists ONLY for fast dev/CI and is never the default demo path.

---

## 1. Technology Stack Decision

### Backend framework: FastAPI (Python 3.11)
- Async ASGI handles the concurrent burst from `simulate.py` without thread-per-request blocking.
- Pydantic v2 gives request/response validation + free OpenAPI docs at `/docs` (usable REST API + a demo surface).
- Python is the native ecosystem for embedding + LLM + stats (numpy, scikit-learn, scipy).
- Why over Flask: Flask is sync-first and needs extra glue for concurrency. Why over Node/Express: the ML/stats ecosystem is Python-first.

### Session store DB: PostgreSQL 16 (AWS RDS db.t3.micro, free tier) with pgvector
- The whole problem is correlation across sessions per user - a relational/analytical workload (GROUP BY user, time-window scans, trend queries). Postgres does this natively.
- ACID + advisory locks make concurrent risk-score updates safe (concurrency constraint).
- JSONB stores flexible metadata; pgvector stores prompt embeddings and does cosine search in the SAME DB.
- One RDS instance, two databases: `detector_db` and `langfuse_db`. Free tier: db.t3.micro single-AZ, 750 hrs/mo for 12 months. pgvector supported on RDS PostgreSQL 15+; first migration runs `CREATE EXTENSION IF NOT EXISTS vector;` in `detector_db`.
- Why over DynamoDB: weak at ad-hoc cross-session aggregates, trend queries, similarity search. Why over MongoDB: we want transactional guarantees on the risk row and relational joins.

### Embedding model: fastembed with BAAI/bge-small-en-v1.5 (384-dim, ONNX)
- fastembed runs a quantized ONNX bge-small with NO PyTorch: ~100-150MB resident vs 500MB-1GB for torch. This is what keeps the detector small enough to co-locate with Langfuse v2 on a 1GB box.
- Same 384-dim vectors, deterministic, offline, zero per-embedding cost.
- Why over OpenAI embeddings: avoids per-call cost/rate limits and keeps embeddings reproducible in CI. (We still use a real LLM for generation/judge.)

### LLM provider (guardrail target + LLM judge): Anthropic Claude
- Model env-driven via `LLM_MODEL` (default: cheapest current Haiku-tier model) to keep cost minimal and avoid rot.
- Two roles: (a) the target LLM the simulated attacker probes; (b) an LLM judge assigning capability level / refusal when rules are ambiguous.
- Abstracted behind an `LLMProvider` interface; OpenAI is a drop-in swap.
- Cost control: `--dry-run` routes to a deterministic local stub for dev/CI ONLY. Default and demo runs use real Claude, satisfying "no mocked responses in the final system."
- Why Claude: strong, well-documented guardrails make adversarial refusals realistic - ideal for a probing scenario.

### Concurrency / background work: FastAPI BackgroundTasks + Postgres advisory locks (NO Celery, NO Redis)
- Detection (embed + cluster + stats + optional judge) runs as a BackgroundTask right after the event is persisted, so ingestion returns fast without a broker.
- Per-user serialization uses `pg_advisory_xact_lock(hashtext(user_id))` inside the scoring transaction. Different users run in parallel; same-user cycles serialize.
- Why over Celery+Redis: on a 1GB box shared with Langfuse v2, one process loading the embedding model once is far lighter and simpler. Documented trade-offs: BackgroundTasks are lost if the process restarts mid-task (acceptable - detection is idempotent, re-triggered on the next event and via `/admin/recompute`); no built-in retry/DLQ (fine at eval scale). The task boundary stays clean so Celery can be reintroduced later.

### Rate limiting: slowapi (in-memory store)
- Per-user-id limit on `POST /v1/events`: **100 requests per user_id per 5 minutes, burst allowance of 20**. In-memory store (single instance, no Redis).
- Why these numbers: Persona B sends up to 150 requests (50 sessions x 3 prompts) and `simulate.py` adds a `--request-delay-ms 500` inter-request delay (default 500ms), so the full run takes ~75 seconds. At 500ms spacing, peak throughput is 2 req/s per user, well under 100/5min = 0.33 req/s sustained. The burst-20 headroom absorbs the initial burst without a 429. The coding agent must use these exact numbers, not "generous."

### Observability + demo UI: self-hosted, pinned Langfuse v2 (optional, behind LANGFUSE_ENABLED)
- Provides the trace/session UI so we do NOT build a custom frontend, and it is self-hosted (no cloud) for maximum weightage.
- Runs as a Langfuse v2 container (`langfuse/langfuse:2`, pinned) pointed at the `langfuse_db` database on RDS. v2 needs only itself + a Postgres DB - light enough to co-locate with the detector.
- Mirror architecture: after persisting to OUR `interactions` table, we send a trace to Langfuse via its SDK and store `langfuse_trace_id` on the row. Alerts embed `trace_url` into evidence for deep-linking.
- Enabled in the AWS deployment, so the evaluator sees the UI live at `:3000`. `LANGFUSE_ENABLED=false` in CI and `simulate.py` so tests stay self-contained.
- Bootstrapping (no manual UI step): use `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` and `LANGFUSE_INIT_PROJECT_SECRET_KEY` env vars that Langfuse v2 supports for pre-seeding API keys on first boot. Set these in `.env.example` with placeholder values; the deployer fills them once before first boot and they are also set in the app's `.env` as `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`. `ec2_bootstrap.sh` documents this step explicitly. This eliminates the chicken-and-egg problem of needing to manually create keys in the UI and restart containers.
- Memory reality (tighter than it looks): Langfuse v2 Next.js peaks at 600-700MB under any real load, not 300MB. fastembed spikes during batch encoding (~130MB resident but higher during encode). SQLAlchemy pool adds 50-100MB. Realistic peak on the box: 900MB-1.1GB for the detector alone, then Langfuse on top. Mitigations baked into deployment:
  1. `ec2_bootstrap.sh` sets `vm.swappiness=10` (`sysctl -w vm.swappiness=10` + persist in `/etc/sysctl.d/99-swappiness.conf`) so the kernel treats swap as a last resort and keeps the detector's hot embedding memory in RAM.
  2. `docker-compose.ec2.yml` sets `mem_limit: 512m` on the Langfuse container, capping it and forcing it to swap its own cold pages rather than competing with the detector for RAM.
  3. Both databases are on RDS; no Postgres runs on the EC2 box.
  4. README and demo notes: run `simulate.py` BEFORE opening the Langfuse UI (not simultaneously) to keep peak memory below the ceiling.
- Fallback (documented, not default): upgrade to a t3.small for the live demo if the box is unstable under the two-service load.

### Containerization: Docker + docker-compose
- Local dev app compose: `api` + `postgres` (pgvector, hosting both `detector_db` and `langfuse_db` locally) + optional `langfuse` (v2). Reproducible with one command.
- On EC2: `api` + `langfuse` (v2) containers, both pointing at RDS.

### Deployment target: AWS EC2 t3.micro (free tier) + Docker Compose, RDS db.t3.micro (free tier)
- EC2 t2/t3.micro: 750 hrs/mo free for 12 months. Runs `api` on 8000 and Langfuse v2 on 3000 (optional nginx). SG opens 22, 8000, 3000 (and 80 if nginx).
- RDS db.t3.micro (pgvector) hosts `detector_db` + `langfuse_db`; SG allows the EC2 SG only.
- Why EC2 over Fargate: Fargate is not free-tier (~$30-60/mo). Evaluator only needs reachable URLs, which EC2 provides - two of them here.
- Why no ElastiCache: not free-tier; Redis removed (advisory locks + in-memory rate limit).
- Why no ALB: ~$16/mo just to exist; expose ports directly or via nginx.

### CI/CD: GitHub Actions
- PR: ruff, mypy, pytest (three detectors in isolation + scorer + API), `simulate.py` against a Postgres service container, all with `LANGFUSE_ENABLED=false` and `--dry-run`.
- Deploy: build image, ship to EC2 over SSH, `docker compose pull && up -d`, run Alembic migrations.
- Why GitHub Actions: zero-infra, native to the repo.

---

## 2. System Architecture

```
                        +--------------------------------------------------+
                        |                  Simulation Harness              |
                        |  simulate.py  (4 personas, seeded, reproducible) |
                        +-----------------------+--------------------------+
                                                | HTTP (REST)
                                                v
+=====================================  AWS EC2 t3.micro (Docker Compose)  ================+
|                                                                                          |
|  +------------------------------ FastAPI API (:8000) ------------------------------+     |
|  |  POST /v1/events (slowapi per-user rate limit)                                   |     |
|  |    1. validate (Pydantic)                                                        |     |
|  |    2. LLMProvider -> real Claude (or --dry-run stub in dev)                        |     |
|  |    3. GuardrailEvaluator -> allowed / blocked / flagged                            |     |
|  |    4. capability tagger -> capability_level (rules; Claude judge if ambiguous)     |     |
|  |    5. EmbeddingService (fastembed) -> 384-d vector                                |     |
|  |    6. persist Session + Interaction to detector_db  = SOURCE OF TRUTH              |     |
|  |    7. [optional] mirror trace to Langfuse v2, store langfuse_trace_id             |     |
|  |    8. schedule BackgroundTask: detection for this user_id                         |     |
|  |    9. return 202 (interaction_id, session_id, guardrail_outcome, risk snapshot)    |     |
|  |                                                                                  |     |
|  |  BackgroundTask: DetectionOrchestrator(user_id)                                   |     |
|  |    a. pg_advisory_xact_lock(hashtext(user_id))                                    |     |
|  |    b. check inactivity: if now-last_event_at > WINDOW -> zero profile, RETURN      |     |
|  |    c. load rolling window from detector_db                                        |     |
|  |    d. ProbingDetector -> signal_p   EscalationDetector -> signal_e                |     |
|  |    e. EnumerationDetector -> signal_n                                              |     |
|  |    f. RiskScorer: decay -> weighted combine -> accumulate                          |     |
|  |    g. write user_risk_profiles + detected_patterns                                |     |
|  |    h. if score >= threshold -> write Alert (+ trace_url evidence)                  |     |
|  |                                                                                  |     |
|  |  GET /v1/users/{id}/risk , /patterns , GET/PATCH /v1/alerts , admin , /health , /ready |
|  +----------------+---------------------------------------------+-------------------+     |
|                   |                                             |                          |
|  +------------ Langfuse v2 server (:3000) ----------+           | structured JSON logs      |
|  |  self-hosted UI + trace ingestion (mirror only)  |           v                          |
|  +----------------+---------------------------------+   docker logs / CloudWatch agent      |
+===================|=========================================|============================+
                    | detector_db (source of truth)          | langfuse_db (mirror)
                    v                                         v
        +-----------------------------------------------------------------+
        |                RDS PostgreSQL db.t3.micro (+ pgvector)           |
        |   detector_db: sessions, interactions(+embedding,               |
        |                langfuse_trace_id), user_risk_profiles,          |
        |                detected_patterns, alerts                        |
        |   langfuse_db: Langfuse v2 internal tables                      |
        +-----------------------------------------------------------------+

Evaluator sees two links from the same box:
  http://EC2_PUBLIC_IP:8000/docs   (detector API)
  http://EC2_PUBLIC_IP:3000        (self-hosted Langfuse v2 UI)
```

Flow: user message -> validate -> real Claude -> guardrail + capability tag -> embed -> persist to `detector_db` (+optional Langfuse mirror) -> BackgroundTask -> three independent detectors -> risk scorer (reset/decay/combine) -> persist risk + patterns -> alert when threshold crossed -> surfaced via `GET /v1/alerts` and deep-linked into Langfuse. Every step logs structured JSON.

---

## 3. Data Models

All tables in `detector_db` (Postgres). Timestamps `TIMESTAMPTZ` (UTC). PKs are UUID v4 unless noted. (Langfuse manages its own tables in `langfuse_db`; we never touch them directly.)

### 3.1 `sessions`
- `id` UUID PK - session handle.
- `user_id` TEXT NOT NULL - the correlation identity.
- `started_at` TIMESTAMPTZ NOT NULL - session start (session-count / inactivity logic).
- `last_event_at` TIMESTAMPTZ NOT NULL - updated per interaction (session boundary detection).
- `interaction_count` INT NOT NULL DEFAULT 0 - denormalized counter.
- `metadata` JSONB - optional client info.
- Index: `idx_sessions_user_started (user_id, started_at DESC)`.
- Why: a "session" is the unit the problem counts (20 / 50 sessions).

### 3.2 `interactions` (the event store, source of truth)
- `id` UUID PK.
- `session_id` UUID FK -> sessions.id.
- `user_id` TEXT NOT NULL (denormalized for per-user scans).
- `ts` TIMESTAMPTZ NOT NULL - event time (all window/sequence logic).
- `prompt` TEXT NOT NULL - raw input.
- `normalized_prompt` TEXT - digits/entities masked (enumeration grouping).
- `response` TEXT - real LLM response.
- `guardrail_outcome` TEXT NOT NULL CHECK IN ('allowed','blocked','flagged') - central to probing.
- `guardrail_reason` TEXT - block category (threat card).
- `capability_level` SMALLINT - 0..4 (escalation).
- `embedding` VECTOR(384) - fastembed bge-small (probing/enumeration similarity).
- `template_signature` TEXT - sha1(normalized_prompt) (enumeration templates).
- `numeric_tokens` JSONB - extracted numbers/IDs (sequentiality).
- `langfuse_trace_id` TEXT NULL - link to the mirrored Langfuse trace (null when Langfuse disabled).
- `latency_ms` INT, `model` TEXT, `is_degraded` BOOL DEFAULT false - operational metadata; `is_degraded` marks LLM-failure fallbacks (never fabricated responses).
- Indexes: `idx_inter_user_ts (user_id, ts DESC)`; `idx_inter_outcome (user_id, guardrail_outcome)`; `idx_inter_template (user_id, template_signature)`; ivfflat/hnsw on `embedding` (`vector_cosine_ops`).
- Why each: user_ts drives the window; outcome speeds block-rate + blocked clustering; template speeds enumeration grouping; vector index speeds cosine clustering.

### 3.3 `user_risk_profiles` (one row per user_id)
- `user_id` TEXT PK - accumulator identity.
- `risk_score` NUMERIC(6,2) NOT NULL DEFAULT 0 - composite 0..100 decayed/accumulated.
- `signal_probing` / `signal_escalation` / `signal_enumeration` NUMERIC(5,4) - last sub-signals 0..1.
- `last_event_at` TIMESTAMPTZ - decay dt + inactivity reset.
- `last_scored_at` TIMESTAMPTZ - decay reference.
- `session_count` INT, `interaction_count` INT - lifetime counts.
- `status` TEXT CHECK IN ('normal','watch','alerted') DEFAULT 'normal'.
- `version` INT NOT NULL DEFAULT 0 - optimistic-lock guard.
- Why: the persisted cross-session accumulator; `version` + advisory lock make concurrent writes safe.

### 3.4 `detected_patterns`
- `id` UUID PK, `user_id` TEXT NOT NULL.
- `pattern_type` TEXT CHECK IN ('probing','escalation','enumeration') NOT NULL.
- `detected_at` TIMESTAMPTZ NOT NULL.
- `signal_strength` NUMERIC(5,4) - the sub-signal value.
- `window_start` / `window_end` TIMESTAMPTZ - evidence window.
- `evidence` JSONB - detector-specific proof (see Section 4), incl. sample `trace_url`s.
- `contributing_interaction_ids` UUID[] - forming events.
- Indexes: `idx_pattern_user_time (user_id, detected_at DESC)`, `idx_pattern_type (pattern_type)`.
- Why: the bonus clusters by technique; this is the per-technique record + evidence store for threat cards.

### 3.5 `alerts`
- `id` UUID PK, `user_id` TEXT NOT NULL, `created_at` TIMESTAMPTZ NOT NULL.
- `risk_score_at_alert` NUMERIC(6,2), `threshold` NUMERIC(6,2) - audit.
- `dominant_pattern` TEXT - highest sub-signal technique.
- `pattern_breakdown` JSONB - three sub-signals + weights (the summary).
- `summary` TEXT - human-readable threat card headline.
- `contributing_pattern_ids` UUID[] -> detected_patterns.
- `status` TEXT CHECK IN ('open','ack','resolved') DEFAULT 'open'.
- Indexes: `idx_alerts_user (user_id, created_at DESC)`, `idx_alerts_status (status)`.
- Why: satisfies "alert when risk crosses a threshold, with a summary"; status supports an ops workflow.

---

## 4. Detection Logic Design

Common windowing: each detector runs on a rolling per-user window - default last `WINDOW_SESSIONS = 30` sessions OR `WINDOW_DAYS = 7` days, whichever spans less. Each detector is an independent, pure, unit-testable function `detect(window, cfg) -> DetectorResult(signal: float 0..1, fired: bool, evidence: dict)` in `detectors/`.

### 4.1 Guardrail boundary probing (`detectors/probing.py`)
- `block_rate = blocked / total` over the window.
- Cluster BLOCKED prompt embeddings with DBSCAN, `metric='cosine'`, `eps=0.25` (cosine similarity >= 0.75), `min_samples=4`.
  - eps=0.25 intentionally: paraphrased probes with bge-small routinely sit at 0.75-0.85 similarity; 0.25 matches the `sim_term` lower bound of 0.75 below.
- Largest cluster C: `mean_sim` = mean pairwise cosine sim within C; `cluster_size = |C|`.
- Gradual-variation guard: require `0.75 <= mean_sim <= 0.985` (excludes identical spam ~1.0 and unrelated < 0.75).
- Fires when: `cluster_size >= 5` AND `mean_sim in [0.75, 0.985]` AND `block_rate >= 0.6`.
- Signal:
  `size_term = min(cluster_size / 20, 1)` (20 blocked variants saturate; matches the 20-session criterion),
  `sim_term = clamp((mean_sim - 0.75) / (0.97 - 0.75), 0, 1)`,
  `signal_p = 0.5*size_term + 0.2*sim_term + 0.3*block_rate`; `*0.3` if not fired.
- Evidence: cluster_size, mean_sim, block_rate, sample prompts, contributing ids, sample trace_urls.

### 4.2 Privilege escalation (`detectors/escalation.py`, `detectors/capability.py`)
- Capability tagging (0..4): 0 general/chit-chat; 1 read/summarize; 2 modify/generate actionable content; 3 elevated/admin/system-level ops or config; 4 execute/exfiltrate/bypass controls. Rule+regex classifier first; low-confidence cases call the Claude judge (strict rubric returning one integer). Cached on the row (computed once).
- Aggregate to a session-level series: `level_i` = max capability_level per session, ordered by session `started_at`. Per-session max denoises within-session chatter.
- Primary trend metrics (robust at small n, unlike Mann-Kendall):
  - `rho` = Spearman correlation between session_index and `level_i`.
  - `slope` = OLS slope of `level_i` vs session_index (np.polyfit).
  - `level_range = max(level) - min(level)`.
  - `nondec_frac` = fraction of consecutive steps with `level_{i+1} >= level_i`.
- Fires when: `rho >= 0.6` AND `level_range >= 2` AND `nondec_frac >= 0.7` AND sessions `>= 5`.
  - Mann-Kendall is kept ONLY as a secondary confirmation when sessions `>= 8`; it never gates small windows. Fixes the low-power problem so Persona D (~10 sessions) fires reliably.
- Signal:
  `signal_e = 0.5*clamp(rho,0,1) + 0.3*(level_range/4) + 0.2*nondec_frac`; `*0.3` if not fired.
- Evidence: session-level series, rho, slope, level_range, nondec_frac, escalating interaction ids.

### 4.3 Systematic enumeration (`detectors/enumeration.py`, `detectors/normalize.py`)
- Normalization: replace digits, sequential IDs, dates, named entities with `<NUM>`/`<ID>`/`<ENT>`; `template_signature = sha1(normalized_prompt)`.
- Group window by `template_signature`. `G` = largest group; `group_size = |G|`; `window_total` = window size; `dominance = group_size / window_total`.
- Sequentiality from `numeric_tokens` of G:
  - `coverage` = distinct_values / (max - min + 1) for the varying numeric slot (range sweep density),
  - `regularity = 1 - normalized_entropy(first_differences of sorted values)` (arithmetic sweeps -> low entropy -> high regularity).
- Semantic check: mean pairwise cosine sim within G `>= 0.9` (enumerators reuse one template).
- Power-user discriminator: legitimate high-volume users have MANY distinct templates -> LOW dominance. Enumerator = high dominance + high regularity; power user = low dominance even at high volume.
- Fires when: `group_size >= 20` AND `dominance >= 0.4` AND `regularity >= 0.7` AND `mean_sim_in_G >= 0.9` AND (`coverage >= 0.6` OR clear arithmetic progression).
- Signal:
  `size_term = min(group_size / 50, 1)` (50 sessions saturate; matches the scraper criterion),
  `signal_n = 0.4*size_term + 0.3*regularity + 0.3*dominance`; `*0.3` if not fired.
- Evidence: template signature, group_size, dominance, regularity, coverage, value range, contributing ids.

### 4.4 Signal-to-risk and decay
- Each cycle each detector returns `signal in [0,1]`; the RiskScorer folds them into the accumulated, time-decayed score (Section 5). Detectors store no risk themselves - they only emit signals + evidence, staying independent and testable.

---

## 5. Risk Score Design

### Composite instantaneous score
- `S_inst = 100 * (w_p*signal_p + w_e*signal_e + w_n*signal_n)`.
- Weights `w_p = 0.35`, `w_e = 0.35`, `w_n = 0.30`: probing and escalation are higher-severity intent signals; enumeration is high-volume but slightly lower per-event severity. Sum to 1 so `S_inst in [0,100]`.

### Accumulation with time decay (continuous-time EWMA)
- Half-life `H = 24h`, `lambda = ln(2)/H`.
- Per cycle at `t` with previous `R_prev` at `t_prev`:
  `dt = t - t_prev`; `R_decayed = R_prev * exp(-lambda*dt)`; `R_new = min(100, R_decayed + alpha*S_inst)`, gain `alpha = 0.6`.
- Sustained multi-session adversarial behavior outpaces decay and climbs; sporadic one-offs decay away. Chosen over a plain average because the problem wants accumulation across sessions with decay over time.

### Alert threshold
- `ALERT_THRESHOLD = 70`; `WATCH_THRESHOLD = 45` sets `status='watch'`.
- Derivation: a single fully-fired detector (signal ~1, weight ~0.35 -> S_inst ~35, contribution ~21/cycle) cannot let a benign one-off reach 70; a strong prober firing across ~4-6 sessions inside a half-life accumulates past 70, while a diverse power user (all signals gated `*0.3`) stays well under 45. Config-driven; finalized by `simulate.py`.

### Inactivity reset (precise — ordering is critical)
- `INACTIVITY_WINDOW = 7 days` (env `INACTIVITY_RESET_SECONDS`).
- Correct execution order in `detection/orchestrator.py`:
  1. Acquire advisory lock.
  2. Check: if `now - last_event_at > INACTIVITY_WINDOW` -> zero `risk_score`, zero the three sub-signals, set `status='normal'`, write the profile, **release lock and RETURN immediately. Do NOT run detectors.**
  3. Only if no reset triggered: run the three detectors, then the scorer (decay -> weighted combine -> accumulate).
- Why the early return is mandatory: if detectors run after the reset, they still see the historical probing/enumeration window in `detector_db` and emit non-zero signals. Those signals feed back into `S_inst`, and `R_new = 0 + alpha*S_inst` immediately re-accumulates above zero in the same cycle — the score never actually reaches 0. The reset clears the slate; detection resumes on the next REAL incoming event (which will have a fresh `last_event_at` and thus not trigger the reset again). This means `test_scoring` must verify that after a reset cycle the profile row reads `risk_score=0`, `signal_probing=0`, `signal_escalation=0`, `signal_enumeration=0`, and that calling recompute again WITHOUT a new real event does NOT re-populate signals.

### Concurrency safety
- Scorer runs in a transaction with `pg_advisory_xact_lock(hashtext(user_id))` + optimistic `version` check. Same-user cycles serialize; different users run in parallel. No Redis.

---

## 6. API Surface (FastAPI)

All bodies are Pydantic; all errors return `{ "error": {"code": str, "message": str, "detail": any} }` with correct HTTP status. `POST /v1/events` is rate-limited per user_id (slowapi, generous burst).

### Ingestion
- `POST /v1/events`
  - Request: `{ user_id: str, session_id: str | null, prompt: str, client_meta?: object }` (null/stale session_id creates a new session).
  - Behavior: real Claude call, guardrail + capability tag, embed, persist, optional Langfuse mirror, schedule detection BackgroundTask.
  - Response `202`: `{ interaction_id, session_id, guardrail_outcome, response_preview, risk_score, status, langfuse_trace_id? }`.
  - Errors: `400` invalid body, `429` rate-limited, `502` LLM provider failure, `503` DB unavailable.

### User risk profile
- `GET /v1/users/{user_id}/risk` -> `200 { user_id, risk_score, status, signals:{probing,escalation,enumeration}, session_count, interaction_count, last_event_at, last_scored_at }`; `404` if unknown.

### Detected patterns / threat card (bonus)
- `GET /v1/users/{user_id}/patterns?type=probing|escalation|enumeration` -> `200 { user_id, threat_card:{dominant_technique, risk_score, summary}, patterns:[{pattern_type, signal_strength, window_start, window_end, evidence, contributing_interaction_ids}] }`.

### Alert feed
- `GET /v1/alerts?status=&user_id=&since=&limit=&offset=` -> `200 { items:[Alert], total }`.
- `GET /v1/alerts/{alert_id}` -> single alert with full `pattern_breakdown` + `summary`.
- `PATCH /v1/alerts/{alert_id}` -> `{ status }` ack/resolve.

### Admin (guarded by `X-Admin-Key`)
- `POST /v1/admin/recompute/{user_id}` -> force a synchronous detection+scoring cycle; returns fresh risk profile.
- `POST /v1/admin/reset/{user_id}` -> manual risk reset (audited).
- `POST /v1/admin/users/{user_id}/set_last_event_at` -> body `{ ts: ISO8601 }`; sets `last_event_at` to a past time so `simulate.py` can drive the inactivity-reset test fully over HTTP (no DB access).
- `GET /v1/config` -> effective thresholds/weights/windows (read-only).
- `PUT /v1/admin/config` -> update runtime-tunable thresholds.

### Ops
- `GET /health` -> `200 {status:"ok"}` liveness.
- `GET /ready` -> checks `detector_db` + embedding model loaded (+ Langfuse reachability only if enabled); `200`/`503`.

---

## 7. Simulation Harness

`simulate.py` is automated and reproducible. Flags: `--seed` (default 42), `--base-url`, `--dry-run` (stub LLM for CI), `--request-delay-ms` (default 500, inter-request sleep in ms). Always runs with `LANGFUSE_ENABLED=false`. It drives four personas via `POST /v1/events`, then asserts via `GET /v1/users/{id}/risk`, `/patterns`, `/v1/alerts`. A "session" = one `session_id` with a small burst (1-3 prompts); a fresh session per iteration models cross-session behavior. At the default 500ms delay, total expected runtime for all four personas is approximately 75 seconds — document this in the README so the evaluator knows what to expect. The 500ms delay keeps all personas well within the 100/5min rate limit (peak ~2 req/s sustained against 0.33/s limit, absorbed by the burst-20 allowance).

### Persona A - Boundary prober (must alert)
- 20 sessions; a base disallowed request paraphrased with gradual drift so embeddings cluster at cosine ~0.78-0.95; all expected `blocked`.
- Expected: ProbingDetector fires; `risk_score >= 70`; alert `dominant_pattern='probing'`.

### Persona B - Data scraper / enumerator (must alert)
- 50 sessions; one template with an incrementing slot ("full record for customer ID {n}", n=1000..1049).
- Expected: EnumerationDetector fires (group_size 50, dominance ~1, regularity ~1, coverage high); alert `dominant_pattern='enumeration'`.

### Persona C - Normal high-volume user (must NOT alert)
- 60+ sessions, diverse legitimate mostly-allowed queries across many topics.
- Expected: all detectors gated (low dominance, low block rate, no trend); `risk_score < 45`; no alert. The explicit false-positive guard.

### Persona D - Privilege escalator (must alert) + edge false-positive user
- Escalator: ~10 sessions climbing capability_level 0->4 monotonically; EscalationDetector fires (Spearman/slope); alert `dominant_pattern='escalation'`.
- Edge user: a power user who occasionally hits one block and asks a couple similar questions; must stay under threshold (verifies detectors do not over-fire on sparse signals).

### Verification
- Prints a results table and exits non-zero on any assertion failure (CI gate): A/B/D alert with correct dominant pattern; C and edge user produce no alert and `risk_score < WATCH_THRESHOLD`.
- Inactivity-reset test: after A alerts, call `POST /v1/admin/users/{A}/set_last_event_at` with a timestamp older than `INACTIVITY_WINDOW`, then `POST /v1/admin/recompute/{A}`, and assert `risk_score == 0` and `status == 'normal'`.

---

## 8. Production Readiness Plan

### Containers
- Local dev (`docker-compose.yml`): `api` (uvicorn/gunicorn FastAPI, embedding model preloaded) + `postgres` (pgvector, init script creates `detector_db` and `langfuse_db`) + optional `langfuse` (`langfuse/langfuse:2`). No worker, no Redis.
- On EC2 (`docker-compose.ec2.yml`): `api` + `langfuse` (v2), both pointing at RDS. Langfuse uses `DATABASE_URL` -> `langfuse_db`; the detector uses `detector_db`. Langfuse container has `mem_limit: 512m`.
- Langfuse v2 env (pinned image `langfuse/langfuse:2`): `NEXTAUTH_URL=http://EC2_PUBLIC_IP:3000`, `NEXTAUTH_SECRET`, `SALT`, `DATABASE_URL` (langfuse_db), `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`, `LANGFUSE_INIT_PROJECT_SECRET_KEY`. These last two pre-seed the API keys on first boot so no manual UI step is needed. The same key values go into the app's `.env` as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST=http://localhost:3000`.

### AWS architecture (free tier)
- EC2 t3.micro (Amazon Linux 2023 + Docker + compose plugin): runs `api` (8000) and Langfuse v2 (3000). SG opens 22, 8000, 3000 (+80 if nginx).
- RDS PostgreSQL 16 db.t3.micro single-AZ; databases `detector_db` (pgvector) + `langfuse_db`; SG allows the EC2 SG only.
- Memory mitigation (in `ec2_bootstrap.sh`): (a) create a 2GB swap file (`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`, persisted in `/etc/fstab`); (b) set `vm.swappiness=10` so the kernel prefers keeping detector RAM resident; (c) `mem_limit: 512m` on the Langfuse container in `docker-compose.ec2.yml`. Both DBs are on RDS so no Postgres runs on the box. Documented fallback: a short-lived t3.small if the live demo box is unstable.
- Logs: containers log structured JSON to stdout -> `docker logs` and optionally the CloudWatch agent (free tier includes basic CloudWatch).
- Justification recap: EC2+compose (not Fargate) for free tier; RDS (not DynamoDB) for correlation/vector queries; advisory locks (not ElastiCache); direct ports (not ALB); Langfuse v2 (not v3) to fit the box.

### Secrets & config
- On EC2: a `.env` file (chmod 600) holds `ANTHROPIC_API_KEY`, RDS creds, `ADMIN_KEY`, Langfuse keys/secrets; injected via docker-compose `env_file`. (AWS Secrets Manager is a documented future upgrade.)
- Non-secret config (weights, thresholds, windows, half-life, `LLM_MODEL`, `LANGFUSE_ENABLED`, `LANGFUSE_HOST`) via env with defaults in `app/config.py` (Pydantic Settings).

### Health checks & errors
- `/health` (liveness), `/ready` (detector_db + model, + Langfuse if enabled). Docker `healthcheck` hits `/health`.
- Central FastAPI exception handlers -> structured JSON + correct codes. LLM calls wrapped with timeout + retry-with-backoff (tenacity); on hard failure, mark `is_degraded=true`, `guardrail_outcome='flagged'` (never fabricate a response) so ingestion degrades gracefully without violating "no mocked responses."
- Detection BackgroundTask is idempotent (keyed by interaction_id); failures logged and re-triggered on the next event or via `/admin/recompute`.
- Langfuse mirror failures are swallowed and logged (never block ingestion or detection) since Langfuse is non-authoritative.

### Deployment scripts
- `deploy/ec2_bootstrap.sh`: install Docker + compose; create 2GB swap + set `vm.swappiness=10`; clone repo; write `.env` (prompts for `ANTHROPIC_API_KEY`, RDS creds, `ADMIN_KEY`, `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`, `LANGFUSE_INIT_PROJECT_SECRET_KEY` so no manual UI step is ever needed).
- `deploy/deploy.sh`: build image -> ship to EC2 (registry or scp `docker save`) -> `docker compose -f docker-compose.ec2.yml up -d`.
- `deploy/migrate.sh`: run Alembic migrations (incl. `CREATE EXTENSION vector`) against `detector_db`; create `langfuse_db` if absent (Langfuse auto-migrates its own schema on first boot).
- `Makefile`: `up`, `down`, `test`, `simulate`, `langfuse-up`, `deploy`. README documents local run, Langfuse toggle, AWS deploy, and the v2 production caveat.

---

## 9. Repository Structure

```
adversarial-detector/
  README.md                       # setup, run, Langfuse v2 toggle + caveat, deploy, architecture
  Makefile                        # up, down, test, simulate, langfuse-up, deploy
  pyproject.toml                  # deps (fastapi, uvicorn, sqlalchemy, alembic, pgvector,
                                  #   fastembed, scikit-learn, scipy, numpy, anthropic,
                                  #   slowapi, tenacity, langfuse), ruff/mypy/pytest config
  .env.example                    # LLM_MODEL, LANGFUSE_ENABLED, LANGFUSE_HOST/keys, ADMIN_KEY, RDS creds
  docker-compose.yml              # local: api + postgres(pgvector, two DBs) + optional langfuse:2
  docker-compose.ec2.yml          # EC2: api + langfuse:2, both pointing at RDS
  docker/
    Dockerfile.api                # api image (model preloaded)
    postgres-init/
      01-create-langfuse-db.sql   # local only: create detector_db + langfuse_db
  alembic/
    env.py                        # migration env (targets detector_db)
    versions/                     # migrations (0001 enables pgvector + creates tables)
  app/
    main.py                       # FastAPI factory, routers, exception handlers, slowapi
    config.py                     # Pydantic Settings (weights, thresholds, windows, secrets)
    logging.py                    # structured JSON logging
    ratelimit.py                  # slowapi limiter (per user_id, in-memory)
    db/
      session.py                  # SQLAlchemy engine/session, pgvector registration
      models.py                   # ORM: Session, Interaction, UserRiskProfile, DetectedPattern, Alert
      repositories.py             # windowed queries, advisory-lock helpers
    schemas/
      events.py, risk.py, alerts.py, patterns.py, config.py  # Pydantic request/response
    api/
      events.py                   # POST /v1/events (+ schedules BackgroundTask)
      risk.py                     # GET /v1/users/{id}/risk
      alerts.py                   # GET/PATCH /v1/alerts
      patterns.py                 # GET /v1/users/{id}/patterns (threat card)
      admin.py                    # recompute/reset/set_last_event_at/config (X-Admin-Key)
      health.py                   # /health, /ready
    llm/
      provider.py                 # LLMProvider interface
      anthropic_provider.py       # real Claude client (target + judge)
      stub_provider.py            # deterministic --dry-run stub (dev/CI only)
      guardrail.py                # GuardrailEvaluator (allowed/blocked/flagged)
    embeddings/
      service.py                  # fastembed (ONNX bge-small) loader + encode()
    detectors/
      base.py                     # DetectorResult + shared window utils
      probing.py                  # ProbingDetector (DBSCAN eps=0.25)
      escalation.py               # EscalationDetector (Spearman/OLS slope)
      enumeration.py              # EnumerationDetector
      capability.py               # capability tagger (rules + Claude judge)
      normalize.py                # normalization + template signatures + numeric extraction
    scoring/
      risk_scorer.py              # inactivity reset + decay + weighted combine + alert
    detection/
      orchestrator.py             # BackgroundTask: advisory lock -> detectors -> scorer -> writes
    integrations/
      langfuse_mirror.py          # optional Langfuse v2 trace mirror (no-op when disabled)
  simulate.py                     # automated 4-persona harness + assertions (CI gate)
  sim/
    personas.py                   # prober, scraper, normal, escalator, edge generators
    runner.py                     # paced session/interaction driver against the API
  tests/
    test_probing.py               # detector in isolation
    test_escalation.py
    test_enumeration.py
    test_scoring.py               # decay, accumulation, threshold, inactivity reset
    test_api.py                   # endpoint contract tests
    test_concurrency.py           # concurrent writes to one user's risk row (advisory lock)
    conftest.py                   # fixtures: test DB, seeded data, stub LLM
  deploy/
    ec2_bootstrap.sh              # Docker + swap + repo + .env on EC2
    deploy.sh, migrate.sh
  .github/workflows/
    ci.yml                        # lint, type-check, tests, simulate (dry-run, langfuse off)
    deploy.yml                    # build + ship to EC2 over SSH
```

---

## 10. Build Sequence

Each phase yields something independently testable; each depends on the prior.

- Phase 0 - Scaffold & config. Repo, pyproject, `config.py`, `logging.py`, `ratelimit.py`, `docker-compose.yml` (api + postgres two-DB init). Test: `docker compose up` starts DB + api; `pytest` collects. Depends on: nothing.
- Phase 1 - Data layer. SQLAlchemy models + Alembic (0001 enables pgvector + tables) + repositories (windowed queries, advisory-lock helper). Test: migrations apply on `detector_db`; repo round-trips an interaction with a 384-d embedding; windowed query returns correct rows. Depends on: Phase 0.
- Phase 2 - Embeddings + normalization. fastembed `EmbeddingService`, `normalize.py`. Test: similar prompts rank higher in cosine sim; enumerated prompts collapse to one `template_signature`; numeric slots extracted. Depends on: Phase 0.
- Phase 3 - LLM provider + guardrail + capability. `anthropic_provider.py`, `stub_provider.py`, `guardrail.py`, `capability.py`. Test (real skipped without key; stub always): disallowed prompt -> `blocked`; admin-style prompt -> `capability_level >= 3`. Depends on: Phase 0.
- Phase 4 - Three detectors (independent). `probing.py`, `escalation.py`, `enumeration.py` as pure functions. Test: each fires only on its target synthetic window and stays silent on benign windows (probing eps=0.25 catches 0.78-sim paraphrases; escalation fires at ~10 sessions via Spearman). Depends on: Phases 1-3.
- Phase 5 - Risk scorer. `risk_scorer.py`: reset (early-return) + decay + weighted combine + alert. Test: `test_scoring` verifies EWMA math, accumulation over cycles, hard reset after inactivity (profile reads risk_score=0 and all sub-signals=0 and detectors were skipped), alert at >= 70, AND that a second recompute without a new real event keeps the score at 0 (early-return ordering enforced). Depends on: Phase 4.
- Phase 6 - Detection orchestration. `detection/orchestrator.py` wiring detectors + scorer + advisory lock + pattern/alert writes, invoked as a BackgroundTask. Test: assert risk row + patterns + alert persisted; `test_concurrency` hammers one user_id and asserts no lost updates. Depends on: Phases 4-5.
- Phase 7 - API surface. All endpoints + exception handlers + rate limit + `/health` + `/ready` + `set_last_event_at`. Test: `test_api` contract tests; end-to-end `POST /v1/events` moves the risk score visible via `GET /v1/users/{id}/risk`. Depends on: Phases 1-6.
- Phase 8 - Simulation harness. `simulate.py` + personas + assertions + inactivity test. Test: `python simulate.py --dry-run` reproduces all four scenarios; A/B/D alert with correct dominant pattern, C + edge stay silent; inactivity reset asserts 0; exits 0. Depends on: Phase 7.
- Phase 9 - Langfuse v2 mirror. `integrations/langfuse_mirror.py` + `langfuse` service (pinned `langfuse/langfuse:2`) in compose, pointed at `langfuse_db`. Test: with `LANGFUSE_ENABLED=true` + Langfuse v2 up, `POST /v1/events` stores a `langfuse_trace_id` and the trace appears in the Langfuse UI at :3000; with the flag false, everything still works and CI stays green. Depends on: Phase 7.
- Phase 10 - Containerize & CI. `Dockerfile.api`, `ci.yml` (lint/type/tests/simulate with dry-run, Langfuse off). Test: CI green on PR. Depends on: Phases 7-8.
- Phase 11 - AWS deploy (free tier). `ec2_bootstrap.sh` (Docker + 2GB swap), `deploy.sh`, `migrate.sh`; EC2 t3.micro + RDS db.t3.micro (`detector_db` + `langfuse_db`); `docker-compose.ec2.yml` runs api + Langfuse v2. Test: EC2 `:8000/health` returns ok; a live `POST /v1/events` works against RDS; the Langfuse UI loads at `:3000` and shows mirrored traces; `simulate.py --base-url http://EC2_IP:8000` passes. Depends on: Phase 10.
  - **Phase 11 report must note:** Persona C (normal user) is the false-negative risk on AWS/RDS — re-run `simulate.py` against the EC2 base URL and confirm `risk_score` stays comfortably below `WATCH_THRESHOLD` (45); latency and BackgroundTask timing on RDS can shift scores. After Phase 8 fixup, local seed-42 runs target a wider margin than the original ~0.93-point headroom.

### Calibration note
After Phase 8, run `simulate.py`; if any scenario mis-fires, tune only the config-driven thresholds/weights (Sections 4-5) - no code changes - until all four success criteria pass. The calibrated values become the committed defaults.

### Demo script (for the presentation)
1. Two links from the same AWS box: detector API at `http://EC2_IP:8000/docs`, self-hosted Langfuse v2 UI at `http://EC2_IP:3000`.
2. Run `simulate.py --base-url http://EC2_IP:8000`; watch traces stream into the self-hosted Langfuse UI.
3. Show raw sessions in Langfuse ("existing single-session tools stop here"), then open `GET /v1/users/{prober}/patterns` and `/v1/alerts` to show the cross-session threat card ("our layer correlates 20 sessions into one probing alert").
4. State the caveat: Langfuse v2 is pinned for a free-tier self-hosted demo; production would run v3 on larger infra, with the detector unchanged because Langfuse is only a mirror.

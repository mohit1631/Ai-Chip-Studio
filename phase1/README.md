# AI Chip Studio — Phase 1 Skeleton (Python / FastAPI)

A runnable backend skeleton covering all 5 sprints of
`01_phase1_core_platform.md`, now with the 4 weak areas from the last
review addressed:

| # | Weak area | Fix |
|---|---|---|
| 1 | No background jobs | Redis + Celery worker queue -- sim/lint/fix/testbench-gen all run as tasks, not inline in the request |
| 2 | No real storage layer | Pluggable backend -- local filesystem (dev) or any S3-compatible API (AWS S3 / MinIO / Cloudflare R2) |
| 3 | AI layer still missing | `ai_lint.py` makes a real Anthropic API call for RTL Review/Bug Detection (regex heuristic demoted to fallback-only) |
| 4 | No DB migrations | Alembic wired up, initial migration hand-written to match `models.py` |

This is still a **skeleton**, not a finished product -- see "What's real
vs. stubbed" below.

## Structure

```text
app/
  main.py              FastAPI app, mounts every router
  config.py             Settings: DB, Redis, storage backend, usage tiers
  celery_app.py          Celery instance (Redis broker + result backend)
  tasks.py                Celery tasks -- the actual background workers
  database.py           SQLAlchemy engine/session
  models.py              User, Project, ProjectMember, UsageRecord, Job
  schemas.py             Pydantic request/response models
  auth.py                 Password hashing + JWT
  deps.py                  get_current_user, usage-limit + project-role dependencies
  routers/
    auth.py                Sprint 5 — register / login / me
    projects.py            Sprint 3 — upload, file tree, dependency graph,
                            enqueue AI lint job, Sprint 5 sharing/members
    code_fix.py            Sprint 1 — enqueues AI Code Fixing jobs
    testbench.py           Sprint 2 — enqueues AI Testbench Generation jobs
    simulation.py          Sprint 4 — enqueues simulation jobs
    jobs.py                 Poll any background job's status/result
  services/
    storage.py              Storage abstraction: local filesystem or S3-compatible
    staging.py              Secure local ZIP/file staging (still needed --
                            zipfile extraction has to happen on local disk)
    project_manager.py     File tree + cross-file dependency graph
    ai_lint.py               Real AI RTL Review / Bug Detection (+ regex fallback)
    ai_client.py             Anthropic API wrapper (mock fallback w/o a key)
    code_fixer.py            Sprint 1 logic: prompt, diff, apply
    testbench_generator.py   Sprint 2 logic: port parsing, prompt
    simulation_runner.py     Verilator/Icarus integration (unchanged from the roadmap bundle)
    jobs.py                  Shared "create Job row + enqueue Celery task" helper
migrations/                Alembic migrations (hand-written initial revision)
alembic.ini
docker-compose.yml         Local Redis + MinIO for dev
requirements.txt
```

## Run it

```bash
pip install -r requirements.txt

# Local dev infra: Redis (required) + MinIO (optional, only if testing s3 storage)
docker compose up -d

# Terminal 1: API
uvicorn app.main:app --reload

# Terminal 2: worker (REQUIRED -- without this, every job stays "pending" forever)
celery -A app.celery_app worker --loglevel=info
```

Then open `http://127.0.0.1:8000/docs` for interactive Swagger UI.

Set `AICHIP_ANTHROPIC_API_KEY` to get real AI responses from code-fix,
testbench generation, and AI lint; without it, those still run as real
background jobs end-to-end, just with a clearly-labeled mock response
standing in for the model output.

**Dev shortcut, no Redis/worker needed:** set `AICHIP_CELERY_TASK_ALWAYS_EAGER=true`
to run tasks inline in the request process. Useful for quick local testing
of the task logic itself -- don't use this anywhere that matters, it
defeats the entire point of the queue.

## Endpoints by sprint

All of Sprint 1/2/4 and the lint part of Sprint 3 now return a **Job**
(`202 Accepted`) instead of the final result -- poll
`GET /jobs/{job_id}` (status only) or `GET /jobs/{job_id}/result`
(status + parsed result) until `status == "success"` or `"failure"`.

| Sprint | Endpoint | Returns |
|---|---|---|
| 5 | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | sync |
| 3 | `POST /projects` (upload .v/.sv or .zip) | sync |
| 3 | `GET /projects`, `GET /projects/{id}` (file tree + deps), `GET /projects/{id}/files/{path}` | sync |
| 3 | `POST /projects/{id}/lint/run` (AI RTL Review / Bug Detection) | **Job** |
| 5 | `POST /projects/{id}/members` (sharing) | sync |
| 1 | `POST /projects/{id}/code-fix/preview`, `POST /projects/{id}/code-fix/apply` | **Job** |
| 2 | `POST /projects/{id}/testbench/generate` | **Job** |
| 4 | `POST /projects/{id}/simulation/run` | **Job** |
| -- | `GET /jobs/{id}`, `GET /jobs/{id}/result`, `GET /projects/{id}/jobs` | sync |

## Database migrations (Alembic)

```bash
alembic upgrade head                              # apply migrations
alembic revision --autogenerate -m "add foo"       # generate the next one from model changes
```

`migrations/env.py` reads the DB URL from `AICHIP_DATABASE_URL` (same
setting the app uses), so there's nothing to keep in sync by hand. The
included `0001_initial` migration was hand-written to match `models.py`
exactly (no live DB was available to autogenerate it from in the
environment this was built in) -- run `alembic revision --autogenerate`
once against a real DB and confirm it comes back empty before trusting
that 1:1 claim blindly.

`AICHIP_AUTO_CREATE_TABLES=true` (the default) still runs
`Base.metadata.create_all()` on startup purely so `uvicorn --reload`
stays a one-command start on a fresh SQLite file. Set it to `false`
anywhere Alembic is the real source of truth (i.e. anywhere there's a
real Postgres instance) -- running both against the same DB works but
makes Alembic's migration history lie about what's actually applied.

## Storage backend (local / S3 / MinIO / R2)

```bash
# Dev default -- nothing to configure
AICHIP_STORAGE_BACKEND=local

# Any S3-compatible API
AICHIP_STORAGE_BACKEND=s3
AICHIP_S3_BUCKET=ai-chip-studio
AICHIP_S3_ENDPOINT_URL=http://localhost:9000   # MinIO/R2 -- omit for real AWS S3
AICHIP_S3_ACCESS_KEY=minioadmin
AICHIP_S3_SECRET_KEY=minioadmin
```

EDA tools (Yosys/Verilator/Icarus) and the regex-based parsers still need
real files on local disk to run against -- the flow is: materialize a
project's files from storage into a throwaway scratch directory, run the
tool, upload any changed/output file back, delete the scratch directory.
See `storage.py`'s module docstring and `tasks.py` for where this happens.

## What's real vs. stubbed

**Real and working:**
- JWT auth, password hashing (bcrypt), role-based project access (owner/admin/editor/viewer)
- Usage-tier metering against the limits in `09_pricing_model.md`, checked at enqueue time and recorded when the job completes
- Secure ZIP staging — path-traversal guard + max-size/file-count/extracted-size caps, checked before extraction
- File tree + cross-file dependency graph (regex-based, tested against multi-module RTL)
- Diff generation/apply via Python's `difflib`
- Real Verilator/Icarus integration via `simulation_runner.py`, now running inside a Celery worker
- Real AI calls for code-fix, testbench generation, and RTL review/bug detection (`ai_lint.py`) when `AICHIP_ANTHROPIC_API_KEY` is set
- Background job queue (Redis + Celery) with DB-tracked status/result/error per job
- Pluggable storage (local filesystem or any S3-compatible API)
- Alembic migrations

**Explicitly stubbed (and clearly marked where):**
- `ai_client.py` mock fallback — without an API key, AI calls return a labeled placeholder so the rest of the pipeline (queueing, storage, DB, usage metering) is still fully exercisable. The prompts themselves are real and have not been tuned against live model output yet.
- `ai_lint.py` regex fallback — same pattern: used only when `MOCK_MODE` is on or the model's JSON response doesn't parse. **Replaces the original skeleton's regex-only stub as the primary path** once a key is configured.
- OAuth login (`auth.py::oauth_login_stub`) — raises `NotImplementedError` on purpose. Needs a real provider SDK and redirect flow; email/password is fully implemented.
- Frontend, waveform viewer UI, Docker worker isolation for untrusted RTL/tool execution — **not in this bundle.** `11_security_roadmap.md`'s "run every tool in a destroyed-after-use Docker worker" requirement still applies before this touches real user uploads in production -- the Celery worker process itself is not sandboxed.

## Known limitations to keep in mind

- `ai_lint.py`'s fallback, `project_manager.py`'s dependency graph, and `testbench_generator.py`'s port parser are **regex-based text heuristics**, not real parsers. Tested against representative multi-module RTL; will misparse sufficiently unusual code. A real parser (`pyverilog`, `slang`) is the natural next step.
- Usage-tier limits are checked once at enqueue time, not per-AI-call *within* a single multi-file project lint job -- a project-wide review can make several AI calls under one limit check. Fine for a skeleton, not fine to ship as-is on a real per-call cap.
- No rate limiting, HTTPS termination, or audit logging — called out as launch-blocking in `11_security_roadmap.md`, not part of this bundle.
- `Job` rows and their `result_json`/`params_json` blobs accumulate forever — no retention/cleanup policy yet.
- Celery's `task_time_limit` plus `simulation_runner.py`'s own subprocess timeout both guard against a wedged EDA tool, but a wedged *worker process* itself still needs real infra-level supervision (e.g. a process manager that restarts it) in production.

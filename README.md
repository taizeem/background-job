# Production-Grade Background Job Processing Platform

A resilient, distributed background job processing platform built from scratch with **Python 3.12**, **Django 5.1**, **Django REST Framework**, **PostgreSQL 16**, and **Redis 7**.

Designed to demonstrate core distributed systems primitives—including lock-free concurrent job claiming (`SELECT ... FOR UPDATE SKIP LOCKED`), lease-based crash recovery, full-jitter exponential backoff, idempotent submission, cron scheduling, sliding-window rate limiting, and structured observability—without relying on Celery or third-party task queues.

## 1. System Architecture

| Component | Process / Runtime | Core Responsibilities |
| :--- | :--- | :--- |
| **REST API Server** | Gunicorn WSGI (`config.wsgi:application`) | Authenticates `X-API-Key` tokens, enforces sliding-window rate limits and active job quotas, validates registered job types and payload size (≤ 64 KB), and atomically persists jobs with `status=PENDING`. |
| **PostgreSQL 16** | Container (`postgres:16-alpine`) | Single Source of Truth (SSOT) for `Job`, `JobAttempt`, `PeriodicTask`, and `ApiKey` tables. Coordinates lock-free concurrent worker claiming via `SELECT ... FOR UPDATE SKIP LOCKED`. |
| **Redis 7** | Container (`redis:7-alpine`) | Ephemeral in-memory store for rolling sliding-window rate-limit `ZSET`s, 5-second TTL queue depth telemetry caches, and 30-second TTL worker liveness heartbeats. |
| **Worker Engine** | CLI Daemon (`manage.py run_worker`) | Polls PostgreSQL for ready jobs, claims a row in a sub-millisecond transaction, executes the registered Python handler outside any database transaction, records `duration_ms` and tracebacks, and applies full-jitter retry logic. |
| **Scheduler & Reaper** | CLI Daemon (`manage.py run_scheduler`) | Evaluates cron expressions (`croniter`) and intervals on `PeriodicTask` rows using deterministic idempotency keys, and sweeps expired worker leases (`ReaperService`) to recover abandoned jobs. |

## 2. Core Engineering Features

- **Zero-Contention Concurrent Claiming**: Workers claim eligible jobs (`status IN ('PENDING', 'RETRYING') AND scheduled_at <= NOW()`) using `SELECT ... FOR UPDATE SKIP LOCKED` ordered by `(-priority, scheduled_at, created_at)`, preventing race conditions and duplicate claims across parallel workers.
- **Strict Transaction Boundaries**: Database transactions are committed immediately after claiming a job (~1 ms). Handler execution runs completely outside open database transactions to prevent connection pool starvation and PostgreSQL `VACUUM` table bloat.
- **Deterministic Finite State Machine (FSM)**: Enforces valid lifecycle transitions (`PENDING` → `CLAIMED` → `RUNNING` → `COMPLETED` / `RETRYING` / `FAILED` / `EXHAUSTED` / `CANCELLED`) at the domain model layer alongside PostgreSQL `CHECK` constraints (`attempt_count >= 0`, `max_attempts >= 1`, `priority >= 0`).
- **Full-Jitter Exponential Backoff & Dead-Letter Queue (DLQ)**: Distinguishes between transient errors (`RetryableJobError`) and permanent errors (`NonRetryableJobError`), scheduling retries using AWS's Full Jitter formula `delay = uniform(0, min(300, 2 * 2^(attempt - 1)))` and routing exhausted jobs into the `EXHAUSTED` dead-letter state.
- **Zombie Lease Reaper & Crash Recovery**: Recovers jobs held by crashed or OOM-killed (`SIGKILL`) workers once `lease_expires_at` passes, logging `ABANDONED` attempts in `JobAttempt` and re-queueing work automatically.
- **Idempotent Submission & Scheduling**: Supports client-supplied `idempotency_key` deduplication (`201 Created` on initial creation, `200 OK` on identical replay, `409 Conflict` on payload mismatch) and deterministic scheduler tick keys (`periodic-{name}-{iso_timestamp}`).
- **Multi-Tenant Security & Resource Safeguards**: SHA-256 hashed API keys (`jp_live_...`), tenant-isolated querysets, per-key `max_active_jobs` quotas, 64 KB payload limits, and Redis sliding-window rate limiting with fail-open resilience.
- **Structured Observability & OpenAPI 3.0**: Single-line JSON logs via `structlog`, `X-Correlation-ID` propagation middleware, Redis-cached telemetry metrics, and interactive Swagger UI documentation.

## 3. Project Directory Structure

```text
job-platform/
├── .github/workflows/ci.yaml       # GitHub Actions CI pipeline (Postgres 16 + Redis 7)
├── Dockerfile                      # Multi-stage Python 3.12 + uv non-root container build
├── compose.yaml                    # 5-service Docker Compose (postgres, redis, api, worker, scheduler)
├── manage.py                       # Django CLI entry point
├── pyproject.toml                  # Dependencies, pytest, and ruff configuration
├── apps/
│   ├── core/                       # Liveness/Readiness probes, Redis pool, CorrelationIdMiddleware
│   ├── accounts/                   # SHA-256 ApiKey model, DRF authentication, RedisRateThrottle
│   ├── jobs/                       # Job & JobAttempt models, FSM, registry, handlers, services, views
│   ├── worker/                     # WorkerEngine, ReaperService, run_worker & run_reaper CLI commands
│   └── scheduler/                  # PeriodicTask model, SchedulerEngine, run_scheduler CLI command
├── config/
│   ├── settings/                   # Modular settings (base.py, development.py, testing.py, production.py)
│   ├── urls.py                     # Root URL router & OpenAPI Swagger/ReDoc endpoints
│   └── wsgi.py                     # Gunicorn WSGI entry point
├── docs/OPERATIONS.md              # Production deployment, pg_dump backup, & incident playbooks
├── loadtests/locustfile.py         # Locust HTTP load testing suite
└── tests/                          # 82 automated unit, integration, and concurrency stress tests
```

## 4. Prerequisites

- **Python 3.12+**
- **`uv`** package manager (`winget install astral-sh.uv`)
- **Docker Desktop** (for PostgreSQL 16 and Redis 7)
- **Git**

## 5. Local Setup & Installation (Windows PowerShell)

```powershell
# 1. Clone the repository and navigate into the project root
git clone https://github.com/taizeem/background-job.git job-platform
Set-Location job-platform

# 2. Create your local .env from the template
Copy-Item .env.example .env

# 3. Install all locked production and development dependencies via uv
uv sync --frozen

# 4. Launch PostgreSQL 16 and Redis 7 in Docker
docker compose up -d

# 5. Apply PostgreSQL database migrations
uv run python manage.py migrate

# 6. Create a developer account and generate a cryptographic API key
uv run python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.create_user('developer', 'dev@company.com', 'password123')"
uv run python manage.py create_api_key developer --name "Local Dev Key" --max-active-jobs 20
```

## 6. Running the Platform Locally

### Start the API Server
```powershell
uv run python manage.py runserver
```

### Start the Background Worker Process
```powershell
# Continuous polling daemon
uv run python manage.py run_worker --worker-id worker-01 --poll-interval 1.0

# Run-once (burst) mode — processes all ready jobs and exits
uv run python manage.py run_worker --burst
```

### Start the Periodic Scheduler & Zombie Lease Reaper
```powershell
# Continuous scheduler and reaper daemon
uv run python manage.py run_scheduler --poll-interval 1.0

# Trigger a single manual zombie lease recovery sweep
uv run python manage.py run_reaper
```

## 7. Full-Stack Docker Deployment

To build the production container image (`USER appuser`) and run all 5 services (`postgres`, `redis`, `api`, `worker`, `scheduler`) inside Docker:

```powershell
# Build and start all 5 services
docker compose --profile fullstack up -d --build

# Check container health status
docker compose --profile fullstack ps

# Tail structured JSON logs across services
docker compose logs -f api worker scheduler

# Stop application containers while keeping Postgres and Redis running
docker compose --profile fullstack stop api worker scheduler
```

## 8. Environment Variables

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `DJANGO_SECRET_KEY` | `local-dev-insecure-...` | Cryptographic signing key (required in production) |
| `DJANGO_DEBUG` | `True` | Debug mode (`False` in production) |
| `DJANGO_SETTINGS_MODULE` | `config.settings.development` | Active settings module (`base`, `development`, `testing`, `production`) |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,0.0.0.0` | Comma-separated allowed HTTP `Host` values |
| `DB_NAME` | `job_platform_dev` | PostgreSQL database name |
| `DB_USER` | `job_user` | PostgreSQL database user |
| `DB_PASSWORD` | `job_password` | PostgreSQL database password |
| `DB_HOST` | `localhost` | PostgreSQL hostname (`postgres` inside Docker Compose) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URI (`redis://redis:6379/0` inside Docker Compose) |

## 9. REST API Endpoints & Examples

When the server is running, interactive OpenAPI 3.0 documentation is available at:
- **Swagger UI**: `http://127.0.0.1:8000/api/docs/`
- **ReDoc**: `http://127.0.0.1:8000/api/redoc/`
- **OpenAPI 3.0 Schema**: `http://127.0.0.1:8000/api/schema/`

| Method | Endpoint | Auth Required | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/health/live/` | No | Liveness probe (returns `200 OK` if process is alive) |
| `GET` | `/api/health/ready/` | No | Readiness probe (pings PostgreSQL and reports Redis status) |
| `POST` | `/api/v1/jobs/` | Yes (`X-API-Key`) | Submits a background job with optional `idempotency_key` and `scheduled_at` |
| `GET` | `/api/v1/jobs/` | Yes (`X-API-Key`) | Lists tenant-owned jobs with pagination and `status` / `job_type` / `priority` filters |
| `GET` | `/api/v1/jobs/{job_id}/` | Yes (`X-API-Key`) | Retrieves full job state, `result`, tracebacks, and nested `attempts` history |
| `POST` | `/api/v1/jobs/{job_id}/cancel/` | Yes (`X-API-Key`) | Cancels an eligible `PENDING` or `RETRYING` job |
| `POST` | `/api/v1/jobs/{job_id}/retry/` | Yes (`X-API-Key`) | Resets `attempt_count=0` and re-queues an `EXHAUSTED` or `FAILED` job |
| `GET` | `/api/v1/jobs/metrics/` | Yes (`X-API-Key`) | Returns Redis-cached queue depths, active worker count, and average latency |

### Example: Submit a Job via PowerShell
```powershell
$headers = @{
    "X-API-Key" = "jp_live_your_secret_key_here"
    "X-Correlation-ID" = "trace-demo-001"
}
$body = @{
    job_type = "files.process"
    payload = @{
        filename = "architecture.txt"
        content = "PostgreSQL SELECT FOR UPDATE SKIP LOCKED provides lock-free queueing"
    }
    priority = 30
    max_attempts = 3
    idempotency_key = "submission-2026-001"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/jobs/" -Method Post -Headers $headers -Body $body -ContentType "application/json"
```

## 10. Testing, Linting, and Schema Verification

```powershell
# 1. Lint and verify code formatting with Ruff
uv run ruff check .
uv run ruff format --check .

# 2. Validate OpenAPI 3.0 specification (0 warnings, 0 errors)
uv run python manage.py spectacular --validate --file schema.yaml
Remove-Item schema.yaml

# 3. Run all 82 automated tests with an 85% minimum coverage gate
uv run pytest --reuse-db --cov=apps --cov=config --cov-report=term-missing --cov-fail-under=85
```

## 11. Performance Benchmark Methodology & Results

Run the built-in multi-threaded benchmark to measure job submission throughput, concurrent worker throughput, and handler execution latency percentiles (p50, p95, p99):

```powershell
uv run python manage.py benchmark_platform --jobs 100 --workers 4
```

### Reference Local Measurements (100 Jobs, 4 Concurrent Worker Threads, PostgreSQL 16 in Docker)
- **Submission (Enqueue) Throughput**: ~180–350 jobs/sec (transactional inserts with payload size and quota checks)
- **Worker Processing Throughput**: ~120–250 jobs/sec (4 concurrent threads claiming via `SELECT ... FOR UPDATE SKIP LOCKED`)
- **Handler Execution Latency**: p50 ≤ 1.5 ms, p95 ≤ 4.0 ms, p99 ≤ 8.0 ms

To run sustained HTTP load testing via Locust:
```powershell
$env:LOCUST_API_KEY="jp_live_your_secret_key_here"
uv run locust -f loadtests/locustfile.py --host=http://127.0.0.1:8000
```

## 12. Key Architecture Decisions & Known Limitations

1. **At-Least-Once Delivery & Handler Idempotency**: If a worker completes an external side effect (e.g., sending an email) and suffers a hard power loss 2ms before committing `status=COMPLETED` to PostgreSQL, `ReaperService` will recover the expired lease and re-queue the job. Consequently, handlers performing external mutations must be idempotent (e.g., passing `job.id` + `attempt_number` or an idempotency token to downstream APIs).
2. **PostgreSQL as Single Source of Truth vs. Redis**: Job statuses are never duplicated in Redis. Storing state in two databases without two-phase commit causes split-brain inconsistencies during network partitions. Redis is strictly restricted to ephemeral sliding-window rate limits, worker heartbeats, and 5-second cached telemetry.
3. **Polling vs. `LISTEN/NOTIFY`**: Workers poll PostgreSQL using the composite index `idx_job_polling` (`status`, `scheduled_at`, `-priority`, `created_at`). While polling adds up to `poll_interval` latency when idle, it is completely stateless and immune to dropped pub/sub notifications.
4. **Cooperative Cancellation**: Jobs in `PENDING` or `RETRYING` states can be cancelled immediately. Jobs actively in `RUNNING` state reject immediate cancellation (`409 Conflict`) to avoid corrupting external resources mid-flight.

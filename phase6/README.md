# AI Chip Studio — Phase 6 Cloud Architecture

Complete backend implementation for Phase 6 per the roadmap spec.

## Architecture

```
Frontend (Next.js)  ──────────────────────────────────────────┐
        │                                                      │  hosted on Render
        ▼                                                      │
   API Gateway (Express/TypeScript)  ─────────────────────────┘
        │
  ┌─────┴─────┐
  ▼           ▼
AI Service  Job Queue (BullMQ + Redis)
  │              │
  │    ┌─────────┴──────────┐
  │    ▼                    ▼
  │ Light Worker Pool   Heavy Worker Pool     ← Render Background Workers
  │ (lint/sim/coverage) (synthesis/formal/PD)
  │    │                    │
  │ Verilator/Icarus    Yosys/SymbiYosys/OpenROAD
  │ (Docker containers) (Docker containers)
  │
  ▼
Ollama (self-hosted, GPU host)  ← OUTSIDE Render (no GPU on Render)
  │
  └── Fallback: Anthropic API (when Ollama unreachable)
```

## Files

```
src/
├── index.ts                  # Express app + all routes wired up
├── types/index.ts            # Shared TypeScript types
├── db/database.ts            # PostgreSQL pool + schema migration
├── middleware/
│   ├── auth.ts               # JWT verification + RBAC
│   └── rateLimiter.ts        # Tier-based rate limits (free/pro/enterprise)
├── services/
│   ├── aiService.ts          # Ollama primary + Anthropic fallback
│   ├── auditService.ts       # Audit log + download audit
│   ├── dockerManager.ts      # Ephemeral per-job containers + resource caps
│   ├── encryption.ts         # AES-256-GCM at rest
│   ├── logger.ts             # Winston + RTL content redaction
│   └── uploadService.ts      # ZIP security validation + encrypt/store
├── api/
│   ├── authRoutes.ts         # /auth/register, /auth/login
│   ├── jobRoutes.ts          # /projects/:id/jobs (submit, poll, download)
│   └── copilotRoutes.ts      # /projects/:id/copilot (generate, debug, properties)
└── workers/
    ├── lightWorker.ts        # BullMQ: lint, bugdetect, simulate, coverage
    └── heavyWorker.ts        # BullMQ: synthesis, formal, physical_design
docker/
├── Dockerfile.light          # Verilator + Icarus image
├── Dockerfile.heavy          # Yosys + SymbiYosys + OpenROAD image
└── docker-compose.yml        # Local dev stack
render.yaml                   # Render deployment config
```

## Security Checklist (Mandatory Before Launch)

| Requirement           | Implemented | Where |
|-----------------------|-------------|-------|
| Docker Isolation      | ✅          | `dockerManager.ts` — ephemeral, destroyed on completion |
| JWT Authentication    | ✅          | `middleware/auth.ts` — every protected route |
| Rate Limiting         | ✅          | `middleware/rateLimiter.ts` — tier-based |
| ZIP Validation        | ✅          | `uploadService.ts` — bomb, traversal, count, size |
| Resource Limits       | ✅          | `dockerManager.ts` — CPU/RAM/timeout at container level |
| Password Hashing      | ✅          | `authRoutes.ts` — argon2id |
| HTTPS                 | ✅          | Render enforces; helmet sets headers |
| Audit Logging         | ✅          | `auditService.ts` — actions + downloads |
| RBAC                  | ✅          | `middleware/auth.ts` — project-level, gates API not just UI |
| Encryption at Rest    | ✅          | `encryption.ts` — AES-256-GCM for all RTL/artifacts |
| Prompt Injection Defense | ✅       | Per-project context only in `copilotRoutes.ts` |
| Download Audit        | ✅          | `auditService.ts` — logged at point-of-serving |

## GPU / Ollama Gap Resolution

All 6 gaps from the roadmap are addressed:

1. **No failover** → Anthropic API fallback in `aiService.ts`, configurable per deployment
2. **No concurrent inference cap** → `AI_CONCURRENCY=3` cap in `aiService.ts` BullMQ worker
3. **No fallback consent logging** → explicit `logger.warn('FALLBACK TRIGGERED')` with project_id
4. **No model versioning** → `getCurrentModelInfo()` exposes current model for metrics
5. **GPU host security** → specified in `.env.example`: authenticated HTTPS, no inbound beyond API
6. **No cold-start plan** → Docker Compose uses persistent Ollama; prod uses always-on or RunPod

## Quick Start (Local)

```bash
# 1. Clone and install
cd phase6
cp .env.example .env   # fill in secrets

# 2. Start all services
cd docker && docker-compose up -d

# 3. API is running at http://localhost:3001
curl http://localhost:3001/health

# 4. Register a user
curl -X POST http://localhost:3001/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"dev@example.com","password":"securepassword123"}'

# 5. Login and get JWT
curl -X POST http://localhost:3001/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"dev@example.com","password":"securepassword123"}'

# 6. Submit a lint job
curl -X POST http://localhost:3001/projects/<project_id>/jobs \
  -H "Authorization: Bearer <token>" \
  -F "file=@./my_design.sv" \
  -F "type=lint"
```

## Job Flow

```
Client → POST /projects/:id/jobs (with .sv/.zip file)
  → ZIP validation (bomb/traversal/size)
  → AES-256 encrypt → store
  → DB record created (status=pending)
  → BullMQ enqueue (light or heavy queue)
  → 202 Accepted {job_id}

Client polls GET /projects/:id/jobs/:jobId
  → Worker picks up → Docker container launched
  → Tool runs (resource-capped)
  → AI analysis (Ollama or fallback)
  → Container destroyed
  → status=completed, output_path set

Client GET /projects/:id/jobs/:jobId/download/:file
  → Auth + RBAC check
  → Download logged (audit)
  → File served
```
 

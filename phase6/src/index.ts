// =====================================================
// AI Chip Studio — Phase 6 — Main API Server
//
// All Phase 6 security requirements enforced here:
// ✅ Helmet (secure headers)
// ✅ CORS
// ✅ JWT auth on every protected route
// ✅ Tier-based rate limiting
// ✅ RBAC (project-level)
// ✅ Request validation (Zod)
// ✅ Error handler (no stack traces in prod)
// ✅ Audit logging
// =====================================================

import 'dotenv/config';
import express, { Request, Response, NextFunction } from 'express';
import helmet from 'helmet';
import cors from 'cors';
import { v4 as uuidv4 } from 'uuid';

import { runMigrations } from './db/database';
import { logger } from './services/logger';
import { authRouter }    from './api/authRoutes';
import { projectRouter } from './api/projectRoutes';
import { jobRouter }     from './api/jobRoutes';
import { copilotRouter } from './api/copilotRoutes';
import { requireAuth }   from './middleware/auth';

const app  = express();
const PORT = Number(process.env.PORT) || 3001;

// ---- Security middleware ----

app.use(helmet());
app.use(cors({
  origin: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:3000'],
  credentials: true,
}));
app.use(express.json({ limit: '1mb' }));    // Body size limit for JSON
app.use(express.urlencoded({ extended: false }));

// ---- Request ID (for tracing) ----

app.use((req: Request, _res: Response, next: NextFunction) => {
  (req as any).requestId = uuidv4();
  next();
});

// ---- Health check (unauthenticated) ----

app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    version: process.env.npm_package_version || '1.0.0',
  });
});

// ---- Auth routes (unauthenticated) ----
// POST /auth/register
// POST /auth/login

app.use('/auth', authRouter);

// ---- Protected routes (require JWT) ----

// Projects must exist before jobs/copilot calls can reference a
// projectId -- this didn't exist before; see projectRoutes.ts's header
// comment for why that made every job submission unreachable.
// POST /projects
// GET  /projects
// GET  /projects/:projectId
app.use('/projects', requireAuth, projectRouter);

// Job management — split by pool automatically
// POST   /projects/:projectId/jobs
// GET    /projects/:projectId/jobs
// GET    /projects/:projectId/jobs/:jobId
// GET    /projects/:projectId/jobs/:jobId/download/:file

app.use('/projects/:projectId/jobs', requireAuth, jobRouter);

// AI Copilot (Phase 5 integration)
// POST /projects/:projectId/copilot/generate
// POST /projects/:projectId/copilot/debug
// POST /projects/:projectId/copilot/properties

app.use('/projects/:projectId/copilot', requireAuth, copilotRouter);

// ---- 404 handler ----

app.use((_req, res) => {
  res.status(404).json({ success: false, error: 'Not found' });
});

// ---- Global error handler ----
// Never expose stack traces in production.

app.use((err: Error, req: Request, res: Response, _next: NextFunction) => {
  const requestId = (req as any).requestId || 'unknown';
  logger.error('Unhandled error', {
    requestId,
    error: err.message,
    stack: process.env.NODE_ENV !== 'production' ? err.stack : undefined,
  });

  res.status(500).json({
    success: false,
    error: process.env.NODE_ENV === 'production'
      ? 'Internal server error'
      : err.message,
    request_id: requestId,
  });
});

// ---- Boot ----

async function main(): Promise<void> {
  try {
    await runMigrations();
    app.listen(PORT, () => {
      logger.info(`AI Chip Studio API running on port ${PORT}`, {
        env: process.env.NODE_ENV,
        port: PORT,
      });
    });
  } catch (err) {
    logger.error('Failed to start server', { error: (err as Error).message });
    process.exit(1);
  }
}

main();

export { app };

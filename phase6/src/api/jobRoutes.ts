// =====================================================
// AI Chip Studio — Phase 6 — Job Routes
//
// POST /projects/:projectId/jobs        — submit job
// GET  /projects/:projectId/jobs/:jobId — poll status
// GET  /projects/:projectId/jobs/:jobId/download/:file — download artifact
// GET  /projects/:projectId/jobs         — list jobs
// =====================================================

import { Router, Request, Response } from 'express';
import multer from 'multer';
import { v4 as uuidv4 } from 'uuid';
import { z } from 'zod';
import { Queue } from 'bullmq';
import Redis from 'ioredis';
import path from 'path';
import fs from 'fs';

import { requireAuth } from '../middleware/auth';
import { requireProjectRole } from '../middleware/auth';
import { tierRateLimit } from '../middleware/rateLimiter';
import { db } from '../db/database';
import { stageUpload, encryptAndStore, decryptForWorker } from '../services/uploadService';
import { logAuditEvent, logDownload } from '../services/auditService';
import { poolForJobType } from '../services/localJobRunner';
import { processLightJob } from '../workers/lightWorker';
import { processHeavyJob } from '../workers/heavyWorker';
import { UploadValidationError } from '../services/uploadService';
import type { JobType, JobPayload, JobStatusResponse } from '../types';

export const jobRouter = Router({ mergeParams: true });

// ---- Job dispatch: free-tier inline processing vs paid-tier queue ----
//
// PROCESS_JOBS_INLINE=true (the free-tier default, see render.yaml): no
// Redis connection or BullMQ Queue is constructed at all -- there's
// nothing free to connect to on Render's free tier anyway (no Background
// Worker, no managed Redis on the free plan). Jobs run synchronously
// inside this request via processJobInline() below.
//
// PROCESS_JOBS_INLINE=false (paid tier): falls back to the original
// enqueue-to-BullMQ-and-let-a-separate-worker-process-it design.
const USE_QUEUE = process.env.PROCESS_JOBS_INLINE !== 'true';

const redis = USE_QUEUE
  ? new Redis(process.env.REDIS_URL || 'redis://localhost:6379' )
  : null;

const LIGHT_QUEUE = USE_QUEUE ? new Queue('light-jobs', { connection: redis as any }) : null;
const HEAVY_QUEUE = USE_QUEUE ? new Queue('heavy-jobs', { connection: redis as any }) : null;

function queueForPool(pool: 'light' | 'heavy'): Queue {
  const queue = pool === 'heavy' ? HEAVY_QUEUE : LIGHT_QUEUE;
  if (!queue) {
    throw new Error('queueForPool() called while PROCESS_JOBS_INLINE=true -- this should never happen, inline mode must not reach the queue branch.');
  }
  return queue;
}

/**
 * Free-tier path: run the job's full pipeline synchronously, in this
 * request, then update the DB row to its final status -- same end state
 * a BullMQ worker would have produced, just without the queue/worker in
 * between. Errors are caught and turned into a 'failed' job row rather
 * than thrown, matching processLightJob/processHeavyJob's own internal
 * try/catch -- so by the time this resolves, the job's DB status is
 * always either 'completed' or 'failed', never left at 'pending'.
 */
async function processJobInline(pool: 'light' | 'heavy', payload: JobPayload): Promise<void> {
  if (pool === 'heavy') {
    await processHeavyJob(payload);
  } else {
    await processLightJob(payload);
  }
}

// Free trial: 5 jobs/month, any job type. Mirrors phase1/app/config.py's
// jobs_per_month=5 -- same business decision, tracked here against
// phase6's own jobs table (phase6 has a separate DB from phase1, not
// phase1's UsageRecord table) by counting rows created since the start
// of the current calendar month.
const FREE_TIER_JOBS_PER_MONTH = Number(process.env.FREE_TIER_JOBS_PER_MONTH) || 5;

async function jobsUsedThisMonth(user_id: string): Promise<number> {
  const result = await db.query(
    `SELECT COUNT(*) AS count FROM jobs
     WHERE user_id = $1 AND created_at >= date_trunc('month', NOW())`,
    [user_id]
  );
  return Number(result.rows[0]?.count ?? 0);
}

// ---- Multer (in-memory, max 50 MB) ----

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 52_428_800 },
});

// ---- Input Validation ----

const VALID_JOB_TYPES = new Set<JobType>([
  'lint', 'bugdetect', 'simulate', 'coverage',
  'formal', 'synthesis', 'physical_design', 'ai_copilot',
]);

const submitJobSchema = z.object({
  type: z.string().refine((t) => VALID_JOB_TYPES.has(t as JobType)),
  options: z.record(z.unknown()).optional().default({}),
});

// ---- POST /projects/:projectId/jobs ----

jobRouter.post(
  '/',
  requireAuth,
  requireProjectRole('editor'),
  tierRateLimit(),
  upload.single('file'),
  async (req: Request, res: Response) => {
    const parsed = submitJobSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ success: false, error: parsed.error.flatten() });
      return;
    }

    if (!req.file) {
      res.status(400).json({ success: false, error: 'No file uploaded' });
      return;
    }

    const { type, options } = parsed.data;
    const jobType = type as JobType;
    const pool = poolForJobType(jobType);

    // Heavy pool (synthesis/formal/physical_design -- Yosys/SymbiYosys/
    // OpenROAD) routinely needs multiple GB of RAM per job (see
    // localJobRunner.ts's POOL_TIMEOUTS_MS comment and the old
    // dockerManager.ts's heavy.memory: '8g' cap). That's far beyond what
    // a free-tier hosting instance has -- running one would crash the
    // whole process, not just that job. Free-tier users get light jobs
    // only (lint/bugdetect/simulate/coverage); heavy jobs require a paid
    // tier, where the hosting plan is sized to actually support them.
    if (pool === 'heavy' && req.user!.tier === 'free') {
      res.status(403).json({
        success: false,
        error: `Job type '${jobType}' requires a paid plan -- synthesis/formal/physical-design jobs `
          + 'need more compute than the free tier provides. Upgrade to run this job type.',
      });
      return;
    }

    // Free trial: 5 jobs/month cap, any job type. Paid tiers (pro,
    // enterprise) aren't capped here -- TIER_LIMITS in rateLimiter.ts
    // already rate-limits request *volume* for them; this is specifically
    // the free-trial usage cap, not a general abuse guard.
    if (req.user!.tier === 'free') {
      const usedThisMonth = await jobsUsedThisMonth(req.user!.sub);
      if (usedThisMonth >= FREE_TIER_JOBS_PER_MONTH) {
        res.status(429).json({
          success: false,
          error: `Free trial limit reached (${FREE_TIER_JOBS_PER_MONTH} jobs/month). `
            + 'Upgrade your plan to keep submitting jobs, or wait until next month.',
        });
        return;
      }
    }

    const job_id = uuidv4();

    try {
      // 1. Stage + validate the upload (ZIP security checks happen here)
      const { staging_path } = await stageUpload(req.file.buffer, req.file.originalname);

      // 2. Encrypt and store permanently
      const encrypted_path = encryptAndStore(staging_path, job_id);

      // 3. Create DB record
      await db.query(
        `INSERT INTO jobs (id, project_id, user_id, type, status, pool, input_path)
         VALUES ($1, $2, $3, $4, 'pending', $5, $6)`,
        [job_id, req.params.projectId, req.user!.sub, jobType, pool, encrypted_path]
      );

      // 4. Process the job.
      //
      // No free-tier Background Worker exists to run this asynchronously
      // (Render's free instance types are Web Service / Static Site /
      // Postgres / Key Value only -- no free Worker type, see render.yaml's
      // header comment). PROCESS_JOBS_INLINE=true runs the job in this
      // same request instead of enqueuing it to BullMQ/Redis and waiting
      // for a separate worker process to pick it up.
      //
      // Trade-off: the HTTP request blocks until the job finishes (light
      // jobs are seconds, which is fine; this is exactly why heavy jobs
      // are blocked above for free-tier users -- a 10-minute OpenROAD run
      // blocking an HTTP request on a free instance would be a disaster
      // either way). Once you're on a paid tier with a real Background
      // Worker + Redis, flip PROCESS_JOBS_INLINE=false to go back to the
      // original enqueue-and-poll flow.
      const payload: JobPayload = {
        job_id,
        project_id: req.params.projectId,
        user_id: req.user!.sub,
        type: jobType,
        input_path: encrypted_path,
        options,
      };

      if (process.env.PROCESS_JOBS_INLINE === 'true') {
        // Await fully -- the job runs to completion right here, so the
        // response sent below can report its real final status.
        await processJobInline(pool, payload);
      } else {
        await queueForPool(pool).add(jobType, payload, {
          jobId: job_id,
          attempts: 2,
          backoff: { type: 'exponential', delay: 5000 },
        });
      }

      // 5. Audit
      await logAuditEvent({
        user_id: req.user!.sub,
        action: 'submit_job',
        resource_type: 'job',
        resource_id: job_id,
        ip_address: req.ip ?? '',
        user_agent: req.headers['user-agent'] ?? '',
        metadata: { type: jobType, pool },
      });

      if (process.env.PROCESS_JOBS_INLINE === 'true') {
        // The job already ran synchronously above -- report its real
        // final status instead of claiming 'pending', which would be
        // wrong by the time this response goes out.
        const finalState = await db.query(
          'SELECT status, error_message FROM jobs WHERE id = $1',
          [job_id]
        );
        const { status: finalStatus, error_message } = finalState.rows[0];
        res.status(finalStatus === 'failed' ? 422 : 200).json({
          success: finalStatus !== 'failed',
          data: { job_id, status: finalStatus, pool, error_message },
          request_id: uuidv4(),
        });
      } else {
        res.status(202).json({
          success: true,
          data: { job_id, status: 'pending', pool },
          request_id: uuidv4(),
        });
      }
    } catch (err) {
      if (err instanceof UploadValidationError) {
        res.status(422).json({ success: false, error: err.message });
        return;
      }
      throw err;
    }
  }
);

// ---- GET /projects/:projectId/jobs/:jobId ----

jobRouter.get(
  '/:jobId',
  requireAuth,
  requireProjectRole('viewer'),
  async (req: Request, res: Response) => {
    const result = await db.query(
      `SELECT id, status, type, created_at, started_at, completed_at, output_path, error_message
       FROM jobs WHERE id = $1 AND project_id = $2`,
      [req.params.jobId, req.params.projectId]
    );

    if (result.rowCount === 0) {
      res.status(404).json({ success: false, error: 'Job not found' });
      return;
    }

    const row = result.rows[0];
    const response: JobStatusResponse = {
      job_id:         row.id,
      status:         row.status,
      type:           row.type,
      created_at:     row.created_at,
      started_at:     row.started_at,
      completed_at:   row.completed_at,
      output_url:     row.output_path
        ? `/projects/${req.params.projectId}/jobs/${row.id}/download`
        : null,
      error_message:  row.error_message,
    };

    res.json({ success: true, data: response, request_id: uuidv4() });
  }
);

// ---- GET /projects/:projectId/jobs ----

jobRouter.get(
  '/',
  requireAuth,
  requireProjectRole('viewer'),
  async (req: Request, res: Response) => {
    const limit  = Math.min(Number(req.query.limit)  || 20, 100);
    const offset = Number(req.query.offset) || 0;

    const result = await db.query(
      `SELECT id, status, type, created_at, completed_at
       FROM jobs WHERE project_id = $1
       ORDER BY created_at DESC LIMIT $2 OFFSET $3`,
      [req.params.projectId, limit, offset]
    );

    res.json({ success: true, data: result.rows, request_id: uuidv4() });
  }
);

// ---- GET /projects/:projectId/jobs/:jobId/download/:file ----
// Logged at point-of-serving (Phase 4 requirement).

jobRouter.get(
  '/:jobId/download/:file',
  requireAuth,
  requireProjectRole('viewer'),
  async (req: Request, res: Response) => {
    const { jobId, file } = req.params;
    const projectId = req.params.projectId;

    const result = await db.query(
      'SELECT output_path, status FROM jobs WHERE id = $1 AND project_id = $2',
      [jobId, projectId]
    );

    if (result.rowCount === 0) {
      res.status(404).json({ success: false, error: 'Job not found' });
      return;
    }

    const { output_path, status } = result.rows[0];

    if (status !== 'completed' || !output_path) {
      res.status(409).json({ success: false, error: 'Job output not ready' });
      return;
    }

    // Sanitize filename to prevent directory traversal
    const safe_file = path.basename(file);
    const file_path = path.join(output_path, safe_file);

    if (!file_path.startsWith(output_path) || !fs.existsSync(file_path)) {
      res.status(404).json({ success: false, error: 'File not found' });
      return;
    }

    // Log download before serving (Phase 4 audit requirement)
    await logDownload(req.user!.sub, jobId, safe_file, req.ip ?? '');

    res.download(file_path, safe_file);
  }
);



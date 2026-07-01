// =====================================================
// AI Chip Studio — Phase 6 — Light Worker Pool
//
// Handles: lint, bugdetect, simulate, coverage
// Docker image: chip-studio-light (Verilator/Icarus)
// Concurrency: higher (fast jobs)
// =====================================================

import { Worker, Job } from 'bullmq';
import Redis from 'ioredis';
import path from 'path';
import fs from 'fs';
import { v4 as uuidv4 } from 'uuid';

import { db } from '../db/database';
import { runInContainer } from '../services/localJobRunner';
import { decryptForWorker } from '../services/uploadService';
import { runAIInference } from '../services/aiService';
import { logger } from '../services/logger';
import type { JobPayload, JobType } from '../types';

// Only construct a Redis connection (and BullMQ Worker, below) when
// actually running the legacy enqueue-and-poll path. On the free-tier
// inline-processing path (PROCESS_JOBS_INLINE=true, see jobRoutes.ts),
// processLightJob is called directly -- no Redis connection needed at
// all, which matters since a free instance has no Redis to connect to.
const USE_QUEUE = process.env.PROCESS_JOBS_INLINE !== 'true';
const redis = USE_QUEUE
  ? new Redis(process.env.REDIS_URL || 'redis://localhost:6379' )
  : null;

const LIGHT_CONCURRENCY = 5;  // More concurrent than heavy pool
const OUTPUT_BASE = process.env.OUTPUT_DIR || '/tmp/chip-studio-outputs';

fs.mkdirSync(OUTPUT_BASE, { recursive: true });

// ---- Job dispatcher ----
// Takes a plain JobPayload now (not BullMQ's Job<JobPayload> wrapper) so
// it can be called either from the BullMQ Worker below (queue path) or
// directly from jobRoutes.ts (free-tier inline path) with the exact same
// function. processLightJobFromQueue adapts the BullMQ shape to this.

export async function processLightJob(payload: JobPayload): Promise<void> {
  const { job_id, type, input_path, project_id, user_id, options } = payload;

  await db.query(
    "UPDATE jobs SET status='active', started_at=NOW() WHERE id=$1",
    [job_id]
  );

  // Decrypt files into a temp work dir
  const work_dir = decryptForWorker(input_path, job_id);
  const out_dir  = path.join(OUTPUT_BASE, job_id);
  fs.mkdirSync(out_dir, { recursive: true });

  try {
    let exit_code: number;
    let stdout: string;
    let stderr: string;

    switch (type as JobType) {
      case 'lint':
      case 'bugdetect':
        ({ exit_code, stdout, stderr } = await runInContainer(
          job_id, 'light',
          ['verilator', '--lint-only', '--Wall', '-sv', '/input/top.sv'],
          work_dir, out_dir
        ));
        // AI bug-detection enhancement: pass verilator output to Ollama
        if (type === 'bugdetect') {
          const aiResult = await runAIInference({
            prompt: `Analyze these Verilator lint warnings for RTL bugs:\n\n${stdout}\n\nList each bug, its severity, and a suggested fix.`,
            system_prompt: 'You are an expert RTL verification engineer. Analyze lint output for real bugs only, not style issues. Format as JSON: [{bug, severity, location, fix}]',
            job_context: { job_id, project_id, type },
            max_tokens: 2048,
          });
          // Write AI analysis alongside tool output
          fs.writeFileSync(path.join(out_dir, 'ai_analysis.json'), aiResult.content);
        }
        break;

      case 'simulate':
        ({ exit_code, stdout, stderr } = await runInContainer(
          job_id, 'light',
          ['bash', '-c', 'cd /input && verilator --cc --exe --build sim_main.cpp *.sv && ./obj_dir/Vtop +vcd'],
          work_dir, out_dir
        ));
        break;

      case 'coverage':
        ({ exit_code, stdout, stderr } = await runInContainer(
          job_id, 'light',
          ['bash', '-c', 'cd /input && verilator --cc --coverage *.sv && make -C obj_dir -f Vtop.mk'],
          work_dir, out_dir
        ));
        // AI coverage hole detection
        const covAI = await runAIInference({
          prompt: `Coverage report:\n${stdout}\n\nIdentify untested code paths and suggest additional test cases.`,
          system_prompt: 'You are a verification engineer. Identify coverage holes and suggest specific stimulus to cover them. Format as JSON: [{path, reason, suggested_test}]',
          job_context: { job_id, project_id, type },
          max_tokens: 2048,
        });
        fs.writeFileSync(path.join(out_dir, 'coverage_suggestions.json'), covAI.content);
        break;

      default:
        throw new Error(`Light worker received unexpected job type: ${type}`);
    }

    // Write raw tool logs
    fs.writeFileSync(path.join(out_dir, 'stdout.log'), stdout);
    fs.writeFileSync(path.join(out_dir, 'stderr.log'), stderr);

    if (exit_code !== 0) {
      await markJobFailed(job_id, `Tool exited with code ${exit_code}:\n${stderr.slice(0, 2000)}`);
      return;
    }

    await markJobComplete(job_id, out_dir);
  } catch (err) {
    const msg = (err as Error).message;
    logger.error('Light job failed', { job_id, error: msg });
    await markJobFailed(job_id, msg);
  } finally {
    // Clean up decrypted work dir
    fs.rmSync(work_dir, { recursive: true, force: true });
  }
}

async function markJobComplete(job_id: string, output_path: string): Promise<void> {
  await db.query(
    "UPDATE jobs SET status='completed', completed_at=NOW(), output_path=$1 WHERE id=$2",
    [output_path, job_id]
  );
  logger.info('Light job complete', { job_id });
}

async function markJobFailed(job_id: string, error_message: string): Promise<void> {
  await db.query(
    "UPDATE jobs SET status='failed', completed_at=NOW(), error_message=$1 WHERE id=$2",
    [error_message.slice(0, 4096), job_id]
  );
  logger.error('Light job marked failed', { job_id });
}

// ---- Start Worker ----
// Only relevant on the legacy enqueue-and-poll (paid-tier, real Redis +
// Background Worker) path. On the free-tier inline path this is never
// called -- jobRoutes.ts calls processLightJob(payload) directly.

export function startLightWorker(): Worker<JobPayload> | null {
  if (!USE_QUEUE || !redis) {
    logger.info('PROCESS_JOBS_INLINE=true -- light worker queue not started (no Redis needed)');
    return null;
  }

  const worker = new Worker<JobPayload>(
    'light-jobs',
    (job: Job<JobPayload>) => processLightJob(job.data),
    {
      connection: redis as any,
      concurrency: LIGHT_CONCURRENCY,
    }
  );

  worker.on('failed', (job, err) => {
    logger.error('BullMQ light job failed', {
      job_id: job?.data.job_id,
      error: err.message,
    });
  });

  logger.info('Light worker pool started', { concurrency: LIGHT_CONCURRENCY });
  return worker;
}

// Entry point when run as a standalone process
if (require.main === module) {
  startLightWorker();
}



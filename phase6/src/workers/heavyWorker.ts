// =====================================================
// AI Chip Studio — Phase 6 — Heavy Worker Pool
//
// Handles: synthesis (Yosys), formal (SymbiYosys), physical_design (OpenROAD)
// Docker image: chip-studio-heavy (Yosys/SymbiYosys/OpenROAD)
// Concurrency: LOW (these jobs are CPU/RAM intensive, run for minutes-hours)
// =====================================================

import { Worker, Job } from 'bullmq';
import Redis from 'ioredis';
import path from 'path';
import fs from 'fs';

import { db } from '../db/database';
import { runInContainer } from '../services/localJobRunner';
import { decryptForWorker } from '../services/uploadService';
import { runAIInference } from '../services/aiService';
import { logger } from '../services/logger';
import type { JobPayload, JobType } from '../types';

// See lightWorker.ts's identical comment: only connect to Redis on the
// legacy enqueue-and-poll path. Heavy jobs are paid-tier only anyway (see
// jobRoutes.ts's tier guard) -- by the time a deployment has a paid tier
// with real compute, it should also have a real Background Worker + Redis,
// so USE_QUEUE should be true in that environment.
const USE_QUEUE = process.env.PROCESS_JOBS_INLINE !== 'true';
const redis = USE_QUEUE
  ? new Redis(process.env.REDIS_URL || 'redis://localhost:6379' )
  : null;

// LOW concurrency — OpenROAD jobs can each consume 8+ GB RAM.
// Running two simultaneously on a 16 GB node starves both.
const HEAVY_CONCURRENCY = 2;

const OUTPUT_BASE = process.env.OUTPUT_DIR || '/tmp/chip-studio-outputs';
fs.mkdirSync(OUTPUT_BASE, { recursive: true });

// ---- Job dispatcher ----
// Plain JobPayload now, not BullMQ's Job<JobPayload> -- see lightWorker.ts.

export async function processHeavyJob(payload: JobPayload): Promise<void> {
  const { job_id, type, input_path, project_id, options } = payload;

  await db.query(
    "UPDATE jobs SET status='active', started_at=NOW() WHERE id=$1",
    [job_id]
  );

  const work_dir = decryptForWorker(input_path, job_id);
  const out_dir  = path.join(OUTPUT_BASE, job_id);
  fs.mkdirSync(out_dir, { recursive: true });

  try {
    let exit_code: number;
    let stdout: string;
    let stderr: string;

    switch (type as JobType) {

      // ---- Synthesis (Yosys) ----
      case 'synthesis': {
        // Generate Yosys TCL synthesis script
        const top_module = (options.top_module as string) || 'top';
        const target_tech = (options.target_tech as string) || 'synth_ice40';
        const yosys_script = `
read_verilog -sv /input/*.sv
synth -top ${top_module}
${target_tech}
stat
write_json /output/netlist.json
write_verilog /output/netlist.v
`.trim();
        fs.writeFileSync(path.join(work_dir, 'synth.ys'), yosys_script);

        ({ exit_code, stdout, stderr } = await runInContainer(
          job_id, 'heavy',
          ['yosys', '-s', '/input/synth.ys', '-l', '/output/yosys.log'],
          work_dir, out_dir
        ));

        // AI synthesis advisor
        if (exit_code === 0) {
          const aiAdvisor = await runAIInference({
            prompt: `Yosys synthesis stats:\n${stdout}\n\nAnalyze: area, timing estimates, and suggest optimizations for the target technology ${target_tech}.`,
            system_prompt: 'You are an RTL synthesis expert. Analyze Yosys output and suggest concrete optimizations. Format as JSON: {area_analysis, timing_estimate, optimization_suggestions:[]}',
            job_context: { job_id, project_id, type },
            max_tokens: 2048,
          });
          fs.writeFileSync(path.join(out_dir, 'synthesis_advisor.json'), aiAdvisor.content);
        }
        break;
      }

      // ---- Formal Verification (SymbiYosys) ----
      case 'formal': {
        const top_module = (options.top_module as string) || 'top';
        const mode = (options.mode as string) || 'prove'; // prove | bmc | cover

        // AI generates the .sby config from design description
        const sbyConfig = await runAIInference({
          prompt: `Generate a SymbiYosys .sby configuration file for formal ${mode} of the top module "${top_module}". The design files are *.sv.`,
          system_prompt: 'You are a formal verification expert. Generate ONLY the .sby config file content, no explanation. Use standard SymbiYosys format.',
          job_context: { job_id, project_id, type },
          max_tokens: 1024,
        });
        fs.writeFileSync(path.join(work_dir, 'formal.sby'), sbyConfig.content);

        ({ exit_code, stdout, stderr } = await runInContainer(
          job_id, 'heavy',
          ['sby', '-f', '/input/formal.sby'],
          work_dir, out_dir
        ));
        break;
      }

      // ---- Physical Design (OpenROAD) ----
      case 'physical_design': {
        const top_module   = (options.top_module   as string) || 'top';
        const pdk_path     = (options.pdk_path     as string) || '/pdk/sky130';
        const target_freq  = (options.target_freq  as number) || 100;  // MHz

        // AI generates OpenROAD TCL flow script
        const openroadScript = await runAIInference({
          prompt: `Generate an OpenROAD TCL script for a full PD flow (floorplan, placement, CTS, routing) for module "${top_module}", PDK at "${pdk_path}", target ${target_freq} MHz. Input netlist is /input/netlist.v.`,
          system_prompt: 'You are a physical design expert. Generate ONLY the TCL script, no explanation. Use OpenROAD 3.x API.',
          job_context: { job_id, project_id, type },
          max_tokens: 4096,
        });
        fs.writeFileSync(path.join(work_dir, 'pd_flow.tcl'), openroadScript.content);

        ({ exit_code, stdout, stderr } = await runInContainer(
          job_id, 'heavy',
          ['openroad', '-exit', '/input/pd_flow.tcl'],
          work_dir, out_dir
        ));
        break;
      }

      default:
        throw new Error(`Heavy worker received unexpected job type: ${type}`);
    }

    fs.writeFileSync(path.join(out_dir, 'stdout.log'), stdout);
    fs.writeFileSync(path.join(out_dir, 'stderr.log'), stderr);

    if (exit_code !== 0) {
      await markJobFailed(job_id, `Tool exited with code ${exit_code}:\n${stderr.slice(0, 2000)}`);
      return;
    }

    await markJobComplete(job_id, out_dir);
  } catch (err) {
    const msg = (err as Error).message;
    logger.error('Heavy job failed', { job_id, error: msg });
    await markJobFailed(job_id, msg);
  } finally {
    fs.rmSync(work_dir, { recursive: true, force: true });
  }
}

async function markJobComplete(job_id: string, output_path: string): Promise<void> {
  await db.query(
    "UPDATE jobs SET status='completed', completed_at=NOW(), output_path=$1 WHERE id=$2",
    [output_path, job_id]
  );
  logger.info('Heavy job complete', { job_id });
}

async function markJobFailed(job_id: string, error_message: string): Promise<void> {
  await db.query(
    "UPDATE jobs SET status='failed', completed_at=NOW(), error_message=$1 WHERE id=$2",
    [error_message.slice(0, 4096), job_id]
  );
}

// ---- Start Worker ----
// Only relevant on the paid-tier queue path -- see lightWorker.ts.

export function startHeavyWorker(): Worker<JobPayload> | null {
  if (!USE_QUEUE || !redis) {
    logger.info('PROCESS_JOBS_INLINE=true -- heavy worker queue not started (no Redis needed)');
    return null;
  }

  const worker = new Worker<JobPayload>(
    'heavy-jobs',
    (job: Job<JobPayload>) => processHeavyJob(job.data),
    {
      connection: redis as any,
      concurrency: HEAVY_CONCURRENCY,
    }
  );

  worker.on('failed', (job, err) => {
    logger.error('BullMQ heavy job failed', {
      job_id: job?.data.job_id,
      error: err.message,
    });
  });

  logger.info('Heavy worker pool started', { concurrency: HEAVY_CONCURRENCY });
  return worker;
}

if (require.main === module) {
  startHeavyWorker();
}



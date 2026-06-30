// =====================================================
// AI Chip Studio — Phase 6 — Docker Worker Manager
//
// ⚠️ DEPRECATED — not imported anywhere as of the free-tier deployment
// work. Needs /var/run/docker.sock, which free-tier PaaS (Render, Fly.io,
// Railway) doesn't grant to a hosted container -- see
// services/localJobRunner.ts, which replaced this with a plain
// child_process-based runner using the same runInContainer() signature.
//
// Kept here as reference for later: once you have a paid tier with a VM
// you fully control (not a shared-container PaaS), this per-job-
// container approach gives you real filesystem/network isolation between
// jobs that localJobRunner.ts explicitly does NOT provide. Re-introduce
// this (or a gVisor/Firecracker-based sandbox) before running untrusted
// multi-tenant RTL at any real scale.
//
// Per-job ephemeral containers. Destroyed on completion
// regardless of success/failure (Phase 1 security requirement).
//
// Resource caps enforced at container level, not inside the tool.
// =====================================================

import Docker from 'dockerode';
import path from 'path';
import { Writable } from 'stream';
import { db } from '../db/database';
import { logger } from '../services/logger';
import type { JobType, WorkerPool } from '../types';

const docker = new Docker({ socketPath: '/var/run/docker.sock' });

const LIGHT_IMAGE = process.env.DOCKER_LIGHT_IMAGE || 'chip-studio-light:latest';
const HEAVY_IMAGE = process.env.DOCKER_HEAVY_IMAGE || 'chip-studio-heavy:latest';
const DOCKER_NET  = process.env.DOCKER_NETWORK     || 'chip-studio-net';

// Per-pool resource caps (enforced at container level, not in tool)
const POOL_CAPS: Record<WorkerPool, { cpuQuota: number; memory: string; timeout_ms: number }> = {
  light: {
    cpuQuota: 100_000,       // 1 CPU (100000 / 100000 period)
    memory:   '2g',
    timeout_ms: 300_000,     // 5 min
  },
  heavy: {
    cpuQuota: 200_000,       // 2 CPUs
    memory:   '8g',
    timeout_ms: 600_000,     // 10 min (OpenROAD can run for hours with uncapped jobs)
  },
};

export function poolForJobType(type: JobType): WorkerPool {
  const heavy: JobType[] = ['synthesis', 'formal', 'physical_design'];
  return heavy.includes(type) ? 'heavy' : 'light';
}

function imageForPool(pool: WorkerPool): string {
  return pool === 'heavy' ? HEAVY_IMAGE : LIGHT_IMAGE;
}

export interface ContainerRunResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  container_id: string;
}

/**
 * Run a job in an ephemeral Docker container.
 * Container is always removed on completion (success or failure).
 */
export async function runInContainer(
  job_id: string,
  pool: WorkerPool,
  cmd: string[],
  input_dir: string,
  output_dir: string
): Promise<ContainerRunResult> {
  const caps = POOL_CAPS[pool];
  const image = imageForPool(pool);

  logger.info('Creating container', { job_id, pool, image });

  const container = await docker.createContainer({
    Image: image,
    Cmd: cmd,
    NetworkingConfig: { EndpointsConfig: { [DOCKER_NET]: {} } },
    HostConfig: {
      // Resource caps at container level (Phase 2 security requirement)
      CpuQuota:     caps.cpuQuota,
      CpuPeriod:    100_000,
      Memory:       parseMemory(caps.memory),
      MemorySwap:   parseMemory(caps.memory), // disable swap
      PidsLimit:    256,
      ReadonlyRootfs: false,  // tools need to write temp files
      NetworkMode:  'none',   // no outbound network from tool containers
      AutoRemove:   false,    // we remove manually to capture logs first

      Binds: [
        `${path.resolve(input_dir)}:/input:ro`,    // read-only input
        `${path.resolve(output_dir)}:/output:rw`,  // writable output
      ],

      // Security hardening
      SecurityOpt: ['no-new-privileges:true'],
      CapDrop:     ['ALL'],
    },
    // No env vars passed — tools don't need them; keeps attack surface small
    Env: [],
    WorkingDir: '/work',
    User: 'nobody',   // non-root inside the container
  });

  // Register container ID in DB so we can kill it on timeout
  await db.query(
    'UPDATE jobs SET docker_container_id = $1, started_at = NOW() WHERE id = $2',
    [container.id, job_id]
  );

  let timedOut = false;
  const timeoutHandle = setTimeout(async () => {
    timedOut = true;
    logger.warn('Container timeout, killing', { job_id, container_id: container.id });
    try { await container.kill(); } catch { /* already stopped */ }
  }, caps.timeout_ms);

  try {
    await container.start();

    // Stream logs while waiting
    const logStream = await container.logs({ follow: true, stdout: true, stderr: true });
    let stdout = '';
    let stderr = '';

    await new Promise<void>((resolve) => {
      const stdoutStream = new Writable({
        write(chunk: Buffer, _enc, cb) {
          stdout += chunk.toString();
          cb();
        },
      });
      const stderrStream = new Writable({
        write(chunk: Buffer, _enc, cb) {
          stderr += chunk.toString();
          cb();
        },
      });
      docker.modem.demuxStream(logStream, stdoutStream, stderrStream);
      logStream.on('end', resolve);
    });

    const info = await container.wait();
    clearTimeout(timeoutHandle);

    if (timedOut) {
      throw new Error(`Container job timed out after ${caps.timeout_ms / 1000}s`);
    }

    return {
      exit_code: info.StatusCode,
      stdout,
      stderr,
      container_id: container.id,
    };
  } finally {
    clearTimeout(timeoutHandle);
    // Always remove, even on error (Phase 1 security: per-job ephemeral containers)
    try {
      await container.remove({ force: true });
      logger.info('Container removed', { job_id, container_id: container.id });
    } catch (removeErr) {
      logger.warn('Container removal failed', {
        job_id,
        container_id: container.id,
        error: (removeErr as Error).message,
      });
    }
  }
}

function parseMemory(s: string): number {
  const units: Record<string, number> = { k: 1024, m: 1024**2, g: 1024**3 };
  const match = s.toLowerCase().match(/^(\d+)([kmg]?)$/);
  if (!match) throw new Error(`Invalid memory spec: ${s}`);
  return Number(match[1]) * (units[match[2]] || 1);
}

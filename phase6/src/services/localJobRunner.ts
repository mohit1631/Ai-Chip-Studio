// =====================================================
// AI Chip Studio — Phase 6 — Local Subprocess Job Runner
//
// Replaces dockerManager.ts's per-job Docker container spawn pattern.
//
// WHY THIS EXISTS: dockerManager.ts needed /var/run/docker.sock to spawn
// an isolated container per job. That requires privileged access to the
// host's Docker daemon -- something no mainstream free-tier PaaS (Render,
// Fly.io, Railway) grants to a web service or worker process, for the
// same reason none of them let you run `docker run` from inside your own
// container: it's a sandbox escape risk (a container with Docker socket
// access can trivially get root on the host). This isn't a Render-
// specific quirk; it's true of every shared-container hosting platform.
//
// WHAT CHANGED: cmd now runs as a plain child_process on the SAME
// container phase6's API/worker is already running in, with Verilator/
// Yosys/etc installed directly into that image (see docker/Dockerfile.api).
// Same job_id/pool/cmd/input_dir/output_dir signature as the old
// runInContainer, so jobRoutes.ts and the workers barely need to change.
//
// WHAT YOU LOSE vs the old per-job-container approach:
//   - No per-job filesystem/network isolation between jobs. Two jobs on
//     this process share the same OS-level namespace. For a single-PaaS-
//     instance, low-concurrency, free-tier deployment this is an
//     acceptable trade-off; it is NOT acceptable once you're running
//     untrusted multi-tenant code at scale -- re-introduce per-job
//     containers (or a gVisor/Firecracker-based sandbox) before that.
//   - CPU quota / memory cgroup limits (CpuQuota, Memory, MemorySwap in
//     the old HostConfig) are gone -- there's no container boundary left
//     to attach them to. The closest available substitute is the
//     timeout_ms kill-on-timeout behavior, kept below, plus whatever
//     resource limit the PaaS itself enforces on the whole instance.
//   - NetworkMode: 'none' (no outbound network from the tool process) is
//     not enforced here either -- if that isolation matters for your
//     threat model, it needs to move to the OS/process level (e.g.
//     network namespaces, which most free PaaS won't grant you the
//     capability to create) or you should not run untrusted RTL through
//     this path until you have it.
// =====================================================

import { spawn } from 'child_process';
import { logger } from './logger';
import type { WorkerPool } from '../types';

const POOL_TIMEOUTS_MS: Record<WorkerPool, number> = {
  light: 300_000,  // 5 min
  heavy: 600_000,  // 10 min (OpenROAD can run long; free tier can't sustain this for many concurrent jobs)
};

export interface ContainerRunResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  container_id: string;  // kept for API-shape compatibility; not a real container ID anymore, just job_id
}

/**
 * Run a job's command as a local subprocess instead of in an ephemeral
 * Docker container. Same call signature as the old runInContainer so
 * callers (jobRoutes.ts, lightWorker.ts, heavyWorker.ts) don't need to
 * change how they invoke this -- only dockerManager.ts's internals are
 * being swapped out.
 *
 * cmd[0] is run directly (no shell), with input_dir/output_dir bound via
 * environment variables INPUT_DIR/OUTPUT_DIR rather than Docker bind
 * mounts -- the light/heavy worker call sites already build their cmd
 * arrays assuming /input and /output paths inside a container, so this
 * also rewrites those literal paths to the real host paths. See the
 * rewriteContainerPaths() note below before changing any cmd arrays in
 * the workers.
 */
export async function runInContainer(
  job_id: string,
  pool: WorkerPool,
  cmd: string[],
  input_dir: string,
  output_dir: string
): Promise<ContainerRunResult> {
  const timeout_ms = POOL_TIMEOUTS_MS[pool];
  const rewritten = cmd.map((arg) => rewriteContainerPaths(arg, input_dir, output_dir));

  logger.info('Starting local subprocess job', { job_id, pool, cmd: rewritten[0] });

  return new Promise<ContainerRunResult>((resolve, reject) => {
    const child = spawn(rewritten[0], rewritten.slice(1), {
      cwd: output_dir,
      env: { ...process.env, INPUT_DIR: input_dir, OUTPUT_DIR: output_dir },
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;

    const timeoutHandle = setTimeout(() => {
      timedOut = true;
      logger.warn('Subprocess timeout, killing', { job_id, pid: child.pid });
      child.kill('SIGKILL');
    }, timeout_ms);

    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });

    child.on('error', (err) => {
      clearTimeout(timeoutHandle);
      reject(err);
    });

    child.on('close', (code) => {
      clearTimeout(timeoutHandle);
      if (timedOut) {
        reject(new Error(`Job timed out after ${timeout_ms / 1000}s`));
        return;
      }
      resolve({
        exit_code: code ?? -1,
        stdout,
        stderr,
        container_id: job_id,
      });
    });
  });
}

/**
 * The old Docker-based cmd arrays reference container-internal paths
 * (/input, /output) that don't exist on a plain subprocess. Rewrite them
 * to the real host directories. This is a stopgap -- once you touch
 * lightWorker.ts/heavyWorker.ts's cmd arrays anyway, prefer building them
 * with the real paths directly instead of relying on this rewrite.
 */
function rewriteContainerPaths(arg: string, input_dir: string, output_dir: string): string {
  return arg.replace(/\/input\b/g, input_dir).replace(/\/output\b/g, output_dir);
}

export function poolForJobType(type: import('../types').JobType): WorkerPool {
  const heavy: import('../types').JobType[] = ['synthesis', 'formal', 'physical_design'];
  return heavy.includes(type) ? 'heavy' : 'light';
}

// =====================================================
// AI Chip Studio — Phase 6 — AI Service
//
// Primary:  Ollama (self-hosted, GPU host)
// Fallback: Anthropic API (when Ollama is unreachable)
//
// Gap resolution per roadmap §"Gaps to Resolve":
//  1. Fallback on GPU host down ✓
//  2. Request queue in front of Ollama ✓
//  3. Fallback consent logging ✓
//  4. Model versioning tracked in config ✓
// =====================================================

import axios, { AxiosError } from 'axios';
import Anthropic from '@anthropic-ai/sdk';
import { Queue, Worker, Job } from 'bullmq';
import Redis from "ioredis";
import type { AIRequest, AIResponse } from '../types';
import { logger, safeLog } from './logger';

// ---- Config ----

const OLLAMA_HOST     = process.env.OLLAMA_HOST || 'http://localhost:11434';
const OLLAMA_MODEL    = process.env.OLLAMA_MODEL || 'qwen2.5-coder:14b';
const OLLAMA_TIMEOUT  = Number(process.env.OLLAMA_TIMEOUT_MS) || 120_000;
const FALLBACK_ENABLED = process.env.OLLAMA_FALLBACK_ENABLED === 'true';

// Separate concurrency cap for AI inference (per roadmap: single GPU can't
// handle many concurrent 14B requests before latency degrades badly).
const AI_CONCURRENCY = 3;

// ---- Redis connection for AI request queue ----

const redisClient = new Redis(process.env.REDIS_URL || "redis://localhost:6379");

// ---- AI Request Queue (BullMQ) ----
// Per roadmap: AI queue is separate from EDA worker queues.

const AI_QUEUE_NAME = 'ai-inference';

let aiQueue: Queue | null = null;

export function getAIQueue(): Queue {
  if (!aiQueue) {
    aiQueue = new Queue(AI_QUEUE_NAME, { connection: redisClient as any });
  }
  return aiQueue;
}

// ---- Core AI call (direct, not queued) ----

async function callOllama(request: AIRequest): Promise<AIResponse> {
  const start = Date.now();
  const response = await axios.post(
    `${OLLAMA_HOST}/api/chat`,
    {
      model: OLLAMA_MODEL,
      messages: [
        { role: 'system', content: request.system_prompt },
        { role: 'user',   content: request.prompt },
      ],
      stream: false,
      options: {
        temperature: request.temperature ?? 0.2,
        num_predict: request.max_tokens ?? 2048,
      },
    },
    { timeout: OLLAMA_TIMEOUT }
  );

  return {
    content: response.data.message.content as string,
    model_used: OLLAMA_MODEL,
    provider: 'ollama',
    latency_ms: Date.now() - start,
    fallback_triggered: false,
  };
}

async function callAnthropic(request: AIRequest): Promise<AIResponse> {
  const start = Date.now();
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const msg = await client.messages.create({
    model: process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-6',
    max_tokens: request.max_tokens ?? 2048,
    system: request.system_prompt,
    messages: [{ role: 'user', content: request.prompt }],
  });

  const content = msg.content
    .filter((b) => b.type === 'text')
    .map((b) => (b as { type: 'text'; text: string }).text)
    .join('');

  return {
    content,
    model_used: process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-6',
    provider: 'anthropic',
    latency_ms: Date.now() - start,
    fallback_triggered: true,
  };
}

/**
 * Run an AI inference request.
 *
 * Strategy:
 *   1. Try Ollama (self-hosted, no RTL leaves infra).
 *   2. On failure, if OLLAMA_FALLBACK_ENABLED, try Anthropic and log the
 *      fallback event so it can trigger consent/disclosure (Gap #3).
 *   3. If both fail, throw — callers should queue the request for retry.
 */
export async function runAIInference(request: AIRequest): Promise<AIResponse> {
  // Security: never log prompt content in prod (Phase 5 requirement)
  safeLog('info', 'AI inference request', {
    job_id: request.job_context.job_id,
    type: request.job_context.type,
    provider_attempt: 'ollama',
  });

  try {
    const result = await callOllama(request);
    logger.info('Ollama inference complete', {
      job_id: request.job_context.job_id,
      latency_ms: result.latency_ms,
    });
    return result;
  } catch (err) {
    const ollamaErr = err as AxiosError;
    logger.warn('Ollama unreachable, checking fallback', {
      job_id: request.job_context.job_id,
      error: ollamaErr.message,
      fallback_enabled: FALLBACK_ENABLED,
    });

    if (!FALLBACK_ENABLED) {
      throw new Error(`Ollama unavailable and fallback is disabled: ${ollamaErr.message}`);
    }

    // GAP #3: Log that RTL is leaving self-hosted infra via fallback.
    // This should trigger a consent/notification step for enterprise tenants.
    logger.warn('FALLBACK TRIGGERED — RTL will be sent to hosted Anthropic API', {
      job_id: request.job_context.job_id,
      project_id: request.job_context.project_id,
      // NOTE: RTL content is NOT logged here (Phase 5 security requirement).
    });

    const result = await callAnthropic(request);
    logger.info('Anthropic fallback inference complete', {
      job_id: request.job_context.job_id,
      latency_ms: result.latency_ms,
    });
    return result;
  }
}

// ---- BullMQ Worker for AI Queue ----
// This allows callers to enqueue AI requests and await results via job polling.

export function startAIWorker(): Worker {
  const worker = new Worker(
    AI_QUEUE_NAME,
    async (job: Job<AIRequest>) => {
      return runAIInference(job.data);
    },
    {
      connection: redisClient as any,
      concurrency: AI_CONCURRENCY,
    }
  );

  worker.on('completed', (job) => {
    logger.info('AI job completed', { job_id: job.data.job_context.job_id });
  });

  worker.on('failed', (job, err) => {
    logger.error('AI job failed', {
      job_id: job?.data.job_context.job_id,
      error: err.message,
    });
  });

  return worker;
}

// ---- Model version tracker (Gap #4) ----
// Call this when swapping Ollama models to surface in metrics.

export function getCurrentModelInfo(): { model: string; host: string } {
  return { model: OLLAMA_MODEL, host: OLLAMA_HOST };
}

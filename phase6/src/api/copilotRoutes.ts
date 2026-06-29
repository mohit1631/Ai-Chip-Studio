// =====================================================
// AI Chip Studio — Phase 6 — AI Copilot Routes
//
// POST /projects/:projectId/copilot/generate   — RTL/UVM/FSM generation
// POST /projects/:projectId/copilot/debug      — debug assistant
// POST /projects/:projectId/copilot/properties — formal property generation
//
// Phase 5 security: per-project context only,
// no cross-user/cross-project data in prompts.
// =====================================================

import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { v4 as uuidv4 } from 'uuid';

import { requireAuth, requireProjectRole } from '../middleware/auth';
import { tierRateLimit } from '../middleware/rateLimiter';
import { runAIInference } from '../services/aiService';
import { db } from '../db/database';
import { logger } from '../services/logger';

export const copilotRouter = Router({ mergeParams: true });

// ---- Input schemas ----

const generateSchema = z.object({
  task: z.enum(['rtl', 'fsm', 'uvm_testbench', 'assertions', 'constraints']),
  description: z.string().min(10).max(4000),
  context_files: z.array(z.string()).max(5).optional(),  // job IDs of existing outputs to use as context
});

const debugSchema = z.object({
  error_log: z.string().max(8000),
  description: z.string().max(2000).optional(),
});

const propertiesSchema = z.object({
  module_name: z.string().max(128),
  description: z.string().max(4000),
  property_style: z.enum(['sva', 'psl']).default('sva'),
});

// ---- System prompts (Phase 5 AI correctness contract) ----

const SYSTEM_PROMPTS: Record<string, string> = {
  rtl: `You are an expert RTL engineer. Generate clean, synthesizable Verilog/SystemVerilog.
Always include: module declaration, port list with directions and types, reset logic, clocking.
Add comments for non-obvious logic. Never generate placeholder or TODO code.
Output ONLY the RTL code, no explanation outside code comments.`,

  fsm: `You are an expert digital design engineer. Generate a complete FSM in SystemVerilog.
Include: state enum, state register (synchronous reset), next-state logic, output logic.
Use two-always-block style. Output ONLY the RTL code.`,

  uvm_testbench: `You are a UVM verification expert. Generate a complete UVM testbench skeleton.
Include: interface, agent (driver/monitor/sequencer), sequence_item, env, test.
Follow UVM 1.2 conventions. Output ONLY the SystemVerilog code.`,

  assertions: `You are a formal verification expert. Generate SystemVerilog Assertions (SVA).
Include: assume properties for inputs, assert properties for design invariants, cover properties for reachability.
Output ONLY the SVA code, embedded in a bind module.`,

  constraints: `You are a static timing analysis expert. Generate Synopsys Design Constraints (SDC).
Include: create_clock, set_input_delay, set_output_delay, false_path where appropriate.
Output ONLY the SDC constraints.`,
};

// ---- POST /copilot/generate ----

copilotRouter.post(
  '/generate',
  requireAuth,
  requireProjectRole('editor'),
  tierRateLimit(),
  async (req: Request, res: Response) => {
    const parsed = generateSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ success: false, error: parsed.error.flatten() });
      return;
    }

    const { task, description, context_files } = parsed.data;
    const project_id = req.params.projectId;
    const job_id = uuidv4();

    // Security: only pull context from jobs that belong to THIS project.
    // Never construct prompts that could include cross-project data.
    let context_snippet = '';
    if (context_files?.length) {
      const result = await db.query(
        `SELECT id, output_path FROM jobs
         WHERE id = ANY($1::uuid[]) AND project_id = $2 AND status = 'completed'`,
        [context_files, project_id]
      );
      // Context is limited to job metadata, not file contents in this route
      // (loading full RTL into prompt requires separate consent check for fallback path)
      context_snippet = result.rows.length
        ? `\n\nRelated completed jobs: ${result.rows.map((r: any) => r.id).join(', ')}`
        : '';
    }

    const prompt = `${description}${context_snippet}`;

    try {
      const result = await runAIInference({
        prompt,
        system_prompt: SYSTEM_PROMPTS[task],
        job_context: { job_id, project_id, type: 'ai_copilot' },
        max_tokens: 4096,
        temperature: 0.15,  // Low temp for code generation
      });

      res.json({
        success: true,
        data: {
          task,
          generated_code: result.content,
          model_used: result.model_used,
          provider: result.provider,
          fallback_triggered: result.fallback_triggered,
          latency_ms: result.latency_ms,
        },
        request_id: job_id,
      });
    } catch (err) {
      logger.error('Copilot generate failed', { job_id, error: (err as Error).message });
      res.status(503).json({
        success: false,
        error: 'AI service unavailable. Request queued for retry.',
      });
    }
  }
);

// ---- POST /copilot/debug ----

copilotRouter.post(
  '/debug',
  requireAuth,
  requireProjectRole('editor'),
  tierRateLimit(),
  async (req: Request, res: Response) => {
    const parsed = debugSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ success: false, error: parsed.error.flatten() });
      return;
    }

    const job_id = uuidv4();
    const { error_log, description } = parsed.data;

    const prompt = description
      ? `Design context: ${description}\n\nError log:\n${error_log}`
      : `Error log:\n${error_log}`;

    const result = await runAIInference({
      prompt,
      system_prompt: `You are an expert RTL debug assistant. Analyze the error log and provide:
1. Root cause analysis
2. Specific lines or constructs causing the issue
3. Exact fix with corrected code snippet
Format as JSON: {root_cause, affected_lines:[], fix_description, corrected_snippet}`,
      job_context: { job_id, project_id: req.params.projectId, type: 'ai_copilot' },
      max_tokens: 2048,
    });

    res.json({
      success: true,
      data: { analysis: result.content, provider: result.provider },
      request_id: job_id,
    });
  }
);

// ---- POST /copilot/properties ----

copilotRouter.post(
  '/properties',
  requireAuth,
  requireProjectRole('editor'),
  tierRateLimit(),
  async (req: Request, res: Response) => {
    const parsed = propertiesSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ success: false, error: parsed.error.flatten() });
      return;
    }

    const job_id = uuidv4();
    const { module_name, description, property_style } = parsed.data;

    const result = await runAIInference({
      prompt: `Module: ${module_name}\n\nDescription: ${description}\n\nGenerate ${property_style.toUpperCase()} formal properties.`,
      system_prompt: SYSTEM_PROMPTS.assertions,
      job_context: { job_id, project_id: req.params.projectId, type: 'ai_copilot' },
      max_tokens: 3000,
      temperature: 0.1,
    });

    res.json({
      success: true,
      data: {
        properties: result.content,
        style: property_style,
        provider: result.provider,
        fallback_triggered: result.fallback_triggered,
      },
      request_id: job_id,
    });
  }
);

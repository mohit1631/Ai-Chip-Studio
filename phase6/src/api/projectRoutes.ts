// =====================================================
// AI Chip Studio — Phase 6 — Project Routes
//
// POST /projects        — create a project (you become its owner)
// GET  /projects        — list projects you're a member of
// GET  /projects/:id    — get one project (membership required)
//
// This file did not exist before -- index.ts mounted jobRoutes.ts
// directly at /projects/:projectId/jobs with no way to ever create a
// projectId in the first place. requireProjectRole() (middleware/auth.ts)
// checks the project_members table for a row, which never existed
// without this -- so every job submission would 403 ("project not
// found or access denied") no matter what, regardless of how correct
// jobRoutes.ts itself was.
// =====================================================

import { Router, Request, Response } from 'express';
import { z } from 'zod';

import { requireAuth, requireProjectRole } from '../middleware/auth';
import { db } from '../db/database';
import { logAuditEvent } from '../services/auditService';

export const projectRouter = Router();

const createProjectSchema = z.object({
  name: z.string().min(1).max(200),
});

// ---- POST /projects ----

projectRouter.post('/', requireAuth, async (req: Request, res: Response) => {
  const parsed = createProjectSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ success: false, error: parsed.error.flatten() });
    return;
  }

  const { name } = parsed.data;
  const owner_id = req.user!.sub;

  const result = await db.query<{ id: string; name: string; created_at: string }>(
    `INSERT INTO projects (name, owner_id) VALUES ($1, $2)
     RETURNING id, name, created_at`,
    [name, owner_id]
  );
  const project = result.rows[0];

  // The creator is the project's owner -- same role name jobRoutes.ts's
  // tier guard and requireProjectRole() everywhere else already expect.
  await db.query(
    `INSERT INTO project_members (project_id, user_id, role) VALUES ($1, $2, 'owner')`,
    [project.id, owner_id]
  );

  await logAuditEvent({
    user_id: owner_id,
    action: 'create_project',
    resource_type: 'project',
    resource_id: project.id,
    ip_address: req.ip ?? '',
    user_agent: req.headers['user-agent'] ?? '',
    metadata: { name },
  });

  res.status(201).json({ success: true, data: project });
});

// ---- GET /projects ----

projectRouter.get('/', requireAuth, async (req: Request, res: Response) => {
  const result = await db.query(
    `SELECT p.id, p.name, p.created_at, pm.role
     FROM projects p
     JOIN project_members pm ON pm.project_id = p.id
     WHERE pm.user_id = $1
     ORDER BY p.created_at DESC`,
    [req.user!.sub]
  );
  res.json({ success: true, data: result.rows });
});

// ---- GET /projects/:projectId ----
// mergeParams isn't needed here since this router owns :projectId
// directly (unlike jobRouter, which is mounted under it).

projectRouter.get(
  '/:projectId',
  requireAuth,
  requireProjectRole('viewer'),
  async (req: Request, res: Response) => {
    const result = await db.query(
      `SELECT id, name, owner_id, created_at FROM projects WHERE id = $1`,
      [req.params.projectId]
    );
    if (result.rows.length === 0) {
      res.status(404).json({ success: false, error: 'Project not found' });
      return;
    }
    res.json({ success: true, data: result.rows[0] });
  }
);

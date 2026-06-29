// =====================================================
// AI Chip Studio — Phase 6 — Auth Middleware
//
// JWT verification + RBAC (project-level).
// Roles gate both API endpoints AND are NOT just UI hints.
// =====================================================

import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import { db } from '../db/database';
import type { JwtPayload, UserRole } from '../types';

declare global {
  namespace Express {
    interface Request {
      user?: JwtPayload;
    }
  }
}

// ---- JWT Authentication ----

export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  const header = req.headers.authorization;
  if (!header?.startsWith('Bearer ')) {
    res.status(401).json({ success: false, error: 'Missing or invalid Authorization header' });
    return;
  }

  const token = header.slice(7);
  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET!) as JwtPayload;
    req.user = payload;
    next();
  } catch {
    res.status(401).json({ success: false, error: 'Invalid or expired token' });
  }
}

// ---- Project-Level RBAC ----

// Role hierarchy: owner > admin > editor > viewer
const ROLE_RANK: Record<UserRole, number> = {
  owner: 4, admin: 3, editor: 2, viewer: 1,
};

function hasMinRole(actual: UserRole, required: UserRole): boolean {
  return ROLE_RANK[actual] >= ROLE_RANK[required];
}

/**
 * Middleware that checks the user's role on a specific project.
 * Expects :projectId in route params.
 */
export function requireProjectRole(minRole: UserRole) {
  return async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    if (!req.user) {
      res.status(401).json({ success: false, error: 'Unauthenticated' });
      return;
    }

    const projectId = req.params.projectId;
    if (!projectId) {
      res.status(400).json({ success: false, error: 'projectId param required' });
      return;
    }

    try {
      // Global owners/admins bypass project-level checks
      if (hasMinRole(req.user.role, 'admin')) {
        next();
        return;
      }

      const result = await db.query<{ role: UserRole }>(
        `SELECT pm.role FROM project_members pm
         WHERE pm.project_id = $1 AND pm.user_id = $2`,
        [projectId, req.user.sub]
      );

      if (result.rowCount === 0) {
        res.status(403).json({ success: false, error: 'Access denied to this project' });
        return;
      }

      const projectRole = result.rows[0].role;
      if (!hasMinRole(projectRole, minRole)) {
        res.status(403).json({
          success: false,
          error: `Requires at least '${minRole}' role on this project`,
        });
        return;
      }

      next();
    } catch (err) {
      next(err);
    }
  };
}

// ---- Global role guard ----

export function requireGlobalRole(minRole: UserRole) {
  return (req: Request, res: Response, next: NextFunction): void => {
    if (!req.user || !hasMinRole(req.user.role, minRole)) {
      res.status(403).json({ success: false, error: 'Insufficient global permissions' });
      return;
    }
    next();
  };
}

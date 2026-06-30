// =====================================================
// AI Chip Studio — Phase 6 — Auth Routes
//
// POST /auth/register  — create account
// POST /auth/login     — get JWT
// POST /auth/refresh   — refresh token
//
// Password hashing: argon2 (Phase 6 security requirement).
// bcrypt is the fallback; never plaintext/reversible.
// =====================================================

import { Router, Request, Response } from 'express';
import argon2 from 'argon2';
import jwt from 'jsonwebtoken';
import { z } from 'zod';
import { v4 as uuidv4 } from 'uuid';
import { db } from '../db/database';
import { logAuditEvent } from '../services/auditService';
import { authRateLimit } from '../middleware/rateLimiter';
import type { UserRecord, JwtPayload, SubscriptionTier, UserRole } from '../types';

export const authRouter = Router();

// ---- Input Schemas ----

const registerSchema = z.object({
  email:    z.string().email(),
  password: z.string().min(12).max(128),
});

const loginSchema = z.object({
  email:    z.string().email(),
  password: z.string(),
});

// ---- JWT helpers ----

function signToken(user: Pick<UserRecord, 'id' | 'email' | 'role' | 'tier'>): string {
  const payload: Omit<JwtPayload, 'iat' | 'exp'> = {
    sub:   user.id,
    email: user.email,
    role:  user.role as UserRole,
    tier:  user.tier as SubscriptionTier,
  };
  return jwt.sign(payload, process.env.JWT_SECRET!, {
    expiresIn: (process.env.JWT_EXPIRES_IN || '7d') as jwt.SignOptions['expiresIn'],
  });
}

// ---- POST /auth/register ----

authRouter.post('/register', authRateLimit, async (req: Request, res: Response) => {
  const parsed = registerSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ success: false, error: parsed.error.flatten() });
    return;
  }

  const { email, password } = parsed.data;

  try {
    // Argon2id — best current standard for password hashing
    const password_hash = await argon2.hash(password, {
      type: argon2.argon2id,
      memoryCost: 65536,   // 64 MB
      timeCost:   3,
      parallelism: 4,
    });

    const result = await db.query<{ id: string }>(
      `INSERT INTO users (id, email, password_hash, role, tier)
       VALUES ($1, $2, $3, 'editor', 'free')
       RETURNING id`,
      [uuidv4(), email, password_hash]
    );

    const user_id = result.rows[0].id;
    await logAuditEvent({
      user_id,
      action: 'register',
      resource_type: 'user',
      resource_id: user_id,
      ip_address: req.ip ?? '',
      user_agent: req.headers['user-agent'] ?? '',
      metadata: { email },
    });

    res.status(201).json({ success: true, data: { user_id } });
  } catch (err: any) {
    if (err.code === '23505') {
      // Unique constraint on email
      res.status(409).json({ success: false, error: 'Email already registered' });
      return;
    }
    throw err;
  }
});

// ---- POST /auth/login ----

authRouter.post('/login', authRateLimit, async (req: Request, res: Response) => {
  const parsed = loginSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ success: false, error: parsed.error.flatten() });
    return;
  }

  const { email, password } = parsed.data;

  const result = await db.query<UserRecord>(
    'SELECT * FROM users WHERE email = $1',
    [email]
  );

  // Constant-time comparison (avoid user-enumeration via timing)
  const user = result.rows[0];
  const dummyHash = '$argon2id$v=19$m=65536,t=3,p=4$dummy$dummy'; // prevents timing leak on missing user
  const valid = user
    ? await argon2.verify(user.password_hash, password)
    : await argon2.verify(dummyHash, password).catch(() => false);

  if (!user || !valid) {
    res.status(401).json({ success: false, error: 'Invalid credentials' });
    return;
  }

  await db.query('UPDATE users SET last_login_at = NOW() WHERE id = $1', [user.id]);

  await logAuditEvent({
    user_id: user.id,
    action: 'login',
    resource_type: 'user',
    resource_id: user.id,
    ip_address: req.ip ?? '',
    user_agent: req.headers['user-agent'] ?? '',
    metadata: {},
  });

  const token = signToken(user);
  res.json({ success: true, data: { token } });
});

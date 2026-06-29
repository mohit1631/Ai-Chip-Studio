// =====================================================
// AI Chip Studio — Phase 6 — Rate Limiting
//
// Tier-based rate limits per 09_pricing_model.md.
// Applied per endpoint, not flat global.
// =====================================================

import rateLimit from 'express-rate-limit';
import { Request, Response } from 'express';
import type { SubscriptionTier } from '../types';

const WINDOW_MS = Number(process.env.RATE_LIMIT_WINDOW_MS) || 3_600_000; // 1 hour

const TIER_LIMITS: Record<SubscriptionTier, number> = {
  free:       Number(process.env.RATE_LIMIT_FREE_MAX)       || 100,
  pro:        Number(process.env.RATE_LIMIT_PRO_MAX)        || 1000,
  enterprise: Number(process.env.RATE_LIMIT_ENTERPRISE_MAX) || 10_000,
};

/**
 * Dynamic rate limiter that reads the user's tier from the JWT payload.
 * Falls back to 'free' limits for unauthenticated routes.
 */
export function tierRateLimit() {
  return rateLimit({
    windowMs: WINDOW_MS,
    // Max is evaluated per-request from the JWT tier
    max: (req: Request) => {
      const tier = (req.user?.tier ?? 'free') as SubscriptionTier;
      return TIER_LIMITS[tier] ?? TIER_LIMITS.free;
    },
    // Key by user ID if authenticated, else by IP
    keyGenerator: (req: Request) => req.user?.sub ?? req.ip ?? 'unknown',
    standardHeaders: true,
    legacyHeaders: false,
    handler: (_req: Request, res: Response) => {
      res.status(429).json({
        success: false,
        error: 'Rate limit exceeded. Upgrade your plan for higher limits.',
      });
    },
  });
}

/**
 * Strict rate limit for auth endpoints to prevent brute force.
 */
export const authRateLimit = rateLimit({
  windowMs: 15 * 60 * 1000,  // 15 minutes
  max: 10,
  keyGenerator: (req) => req.ip ?? 'unknown',
  standardHeaders: true,
  legacyHeaders: false,
  handler: (_req, res) => {
    res.status(429).json({
      success: false,
      error: 'Too many authentication attempts. Try again in 15 minutes.',
    });
  },
});

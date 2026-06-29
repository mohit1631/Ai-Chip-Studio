// =====================================================
// AI Chip Studio — Phase 6 — Logger
// =====================================================

import winston from 'winston';

const { combine, timestamp, json, colorize, simple } = winston.format;

const isDev = process.env.NODE_ENV !== 'production';

export const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: combine(
    timestamp(),
    isDev ? combine(colorize(), simple()) : json()
  ),
  transports: [
    new winston.transports.Console(),
  ],
  // Never log prompt contents in production (Phase 5 security requirement)
  // Redact sensitive fields at the transport level
});

// Helper: log without leaking RTL/prompt content
export function safeLog(
  level: 'info' | 'warn' | 'error',
  message: string,
  meta?: Record<string, unknown>
): void {
  const safeMeta = meta ? redactSensitive(meta) : undefined;
  logger[level](message, safeMeta);
}

function redactSensitive(obj: Record<string, unknown>): Record<string, unknown> {
  const REDACTED_KEYS = new Set([
    'prompt', 'rtl', 'verilog', 'systemverilog', 'password',
    'password_hash', 'api_key', 'jwt', 'token',
  ]);
  return Object.fromEntries(
    Object.entries(obj).map(([k, v]) =>
      REDACTED_KEYS.has(k.toLowerCase()) ? [k, '[REDACTED]'] : [k, v]
    )
  );
}

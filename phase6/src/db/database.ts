// =====================================================
// AI Chip Studio — Phase 6 — Database (PostgreSQL)
// =====================================================

import { Pool, PoolClient } from 'pg';
import { logger } from '../services/logger';

// ---- Connection Pool ----

export const db = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 20,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
  ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: true } : false,
});

db.on('error', (err) => {
  logger.error('Unexpected DB pool error', { error: err.message });
});

// ---- Transaction Helper ----

export async function withTransaction<T>(
  fn: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await db.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

// ---- Schema Migration ----

const MIGRATION_SQL = `
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users
CREATE TABLE IF NOT EXISTS users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          TEXT UNIQUE NOT NULL,
  password_hash  TEXT NOT NULL,
  role           TEXT NOT NULL DEFAULT 'editor'
                   CHECK (role IN ('owner','admin','editor','viewer')),
  tier           TEXT NOT NULL DEFAULT 'free'
                   CHECK (tier IN ('free','pro','enterprise')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_login_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Projects
CREATE TABLE IF NOT EXISTS projects (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT NOT NULL,
  owner_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  encryption_key_id  TEXT,   -- enterprise: customer-managed key ref
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id);

-- Project Members (RBAC)
CREATE TABLE IF NOT EXISTS project_members (
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'viewer'
                CHECK (role IN ('owner','admin','editor','viewer')),
  PRIMARY KEY (project_id, user_id)
);

-- Jobs
CREATE TABLE IF NOT EXISTS jobs (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id              UUID NOT NULL REFERENCES users(id),
  type                 TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','active','completed','failed','cancelled')),
  pool                 TEXT NOT NULL CHECK (pool IN ('light','heavy')),
  input_path           TEXT NOT NULL,
  output_path          TEXT,
  error_message        TEXT,
  docker_container_id  TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at           TIMESTAMPTZ,
  completed_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);

-- Audit Log
CREATE TABLE IF NOT EXISTS audit_log (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID REFERENCES users(id),
  action         TEXT NOT NULL,
  resource_type  TEXT NOT NULL,
  resource_id    TEXT NOT NULL,
  ip_address     TEXT,
  user_agent     TEXT,
  metadata       JSONB DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- Download Audit (Phase 4 requirement: who downloaded GDS/DEF/LEF)
CREATE TABLE IF NOT EXISTS download_audit (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id),
  job_id       UUID NOT NULL REFERENCES jobs(id),
  file_name    TEXT NOT NULL,
  ip_address   TEXT,
  downloaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dl_audit_job ON download_audit(job_id);
`;

export async function runMigrations(): Promise<void> {
  const client = await db.connect();
  try {
    logger.info('Running DB migrations...');
    await client.query(MIGRATION_SQL);
    logger.info('DB migrations complete');
  } finally {
    client.release();
  }
}

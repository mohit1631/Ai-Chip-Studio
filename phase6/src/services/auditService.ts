// =====================================================
// AI Chip Studio — Phase 6 — Audit Log Service
//
// Phase 4 requirement: download audit for GDS/DEF/LEF.
// Phase 6 requirement: general RBAC/auth audit trail.
// =====================================================

import { db } from '../db/database';
import { logger } from './logger';
import type { AuditLogEntry } from '../types';

export async function logAuditEvent(
  entry: Omit<AuditLogEntry, 'id' | 'created_at'>
): Promise<void> {
  try {
    await db.query(
      `INSERT INTO audit_log (user_id, action, resource_type, resource_id, ip_address, user_agent, metadata)
       VALUES ($1, $2, $3, $4, $5, $6, $7)`,
      [
        entry.user_id,
        entry.action,
        entry.resource_type,
        entry.resource_id,
        entry.ip_address,
        entry.user_agent,
        JSON.stringify(entry.metadata),
      ]
    );
  } catch (err) {
    // Audit logging must never crash the main flow
    logger.error('Failed to write audit log', { error: (err as Error).message });
  }
}

/**
 * Log a file download — required for GDS/DEF/LEF artifacts (Phase 4).
 * Logged at point-of-serving, not just job completion.
 */
export async function logDownload(
  user_id: string,
  job_id: string,
  file_name: string,
  ip_address: string
): Promise<void> {
  try {
    await db.query(
      `INSERT INTO download_audit (user_id, job_id, file_name, ip_address)
       VALUES ($1, $2, $3, $4)`,
      [user_id, job_id, file_name, ip_address]
    );

    // Also write to general audit log for unified trail
    await logAuditEvent({
      user_id,
      action: 'download',
      resource_type: 'job_artifact',
      resource_id: job_id,
      ip_address,
      user_agent: '',
      metadata: { file_name },
    });
  } catch (err) {
    logger.error('Failed to write download audit', { error: (err as Error).message });
  }
}

export async function getDownloadAudit(job_id: string) {
  const result = await db.query(
    `SELECT user_id, file_name, ip_address, downloaded_at
     FROM download_audit WHERE job_id = $1 ORDER BY downloaded_at DESC`,
    [job_id]
  );
  return result.rows;
}

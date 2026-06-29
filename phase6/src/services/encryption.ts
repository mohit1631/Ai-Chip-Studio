// =====================================================
// AI Chip Studio — Phase 6 — Encryption Service
// AES-256-GCM at rest (Phase 3 security requirement)
// =====================================================

import crypto from 'crypto';

const ALGORITHM = 'aes-256-gcm';
const IV_BYTES  = 16;
const TAG_BYTES = 16;

function getKey(): Buffer {
  const hexKey = process.env.ENCRYPTION_KEY;
  if (!hexKey || hexKey.length !== 64) {
    throw new Error('ENCRYPTION_KEY must be a 32-byte hex string (64 hex chars)');
  }
  return Buffer.from(hexKey, 'hex');
}

/**
 * Encrypt a Buffer (file content) with AES-256-GCM.
 * Returns: IV (16 bytes) + ciphertext + auth tag (16 bytes), all concatenated.
 */
export function encryptBuffer(plaintext: Buffer): Buffer {
  const key = getKey();
  const iv  = crypto.randomBytes(IV_BYTES);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();

  return Buffer.concat([iv, encrypted, tag]);
}

/**
 * Decrypt a Buffer that was encrypted with encryptBuffer().
 */
export function decryptBuffer(ciphertext: Buffer): Buffer {
  const key = getKey();
  const iv       = ciphertext.subarray(0, IV_BYTES);
  const tag      = ciphertext.subarray(ciphertext.length - TAG_BYTES);
  const body     = ciphertext.subarray(IV_BYTES, ciphertext.length - TAG_BYTES);

  const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(tag);

  return Buffer.concat([decipher.update(body), decipher.final()]);
}

/**
 * Encrypt a UTF-8 string (for small metadata fields, not for large file bodies).
 * Returns a base64 string safe to store in Postgres.
 */
export function encryptString(plaintext: string): string {
  return encryptBuffer(Buffer.from(plaintext, 'utf8')).toString('base64');
}

export function decryptString(base64Cipher: string): string {
  return decryptBuffer(Buffer.from(base64Cipher, 'base64')).toString('utf8');
}

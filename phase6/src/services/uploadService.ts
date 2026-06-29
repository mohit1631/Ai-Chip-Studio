// =====================================================
// AI Chip Studio — Phase 6 — File Upload Service
//
// ZIP security: bomb protection, path traversal,
// file count, extracted size (Phase 1 security).
// =====================================================

import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { Readable } from 'stream';
import AdmZip from 'adm-zip'; // npm: adm-zip
import { encryptBuffer } from './encryption';
import { logger } from './logger';

// ---- Limits (matches simulation_runner.py's existing caps) ----

const MAX_ZIP_SIZE_BYTES      = Number(process.env.MAX_ZIP_SIZE_BYTES)      || 52_428_800;  // 50 MB
const MAX_ZIP_FILE_COUNT      = Number(process.env.MAX_ZIP_FILE_COUNT)      || 500;
const MAX_EXTRACTED_SIZE_BYTES= Number(process.env.MAX_EXTRACTED_SIZE_BYTES)|| 524_288_000; // 500 MB
const UPLOAD_DIR              = process.env.UPLOAD_DIR                      || '/tmp/chip-studio-uploads';

const ALLOWED_EXTENSIONS = new Set(['.v', '.sv', '.vhd', '.vhdl', '.tcl', '.sdc', '.xdc', '.def', '.lef']);

fs.mkdirSync(UPLOAD_DIR, { recursive: true });

export class UploadValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UploadValidationError';
  }
}

/**
 * Validate and stage an uploaded file.
 * Returns the local staging path (temp, pre-encryption).
 */
export async function stageUpload(
  buffer: Buffer,
  originalName: string
): Promise<{ staging_path: string; file_hash: string }> {

  const ext = path.extname(originalName).toLowerCase();

  // ZIP handling
  if (ext === '.zip') {
    return stageZip(buffer);
  }

  // Single RTL file
  if (!ALLOWED_EXTENSIONS.has(ext)) {
    throw new UploadValidationError(`Unsupported file type: ${ext}`);
  }

  const file_hash = crypto.createHash('sha256').update(buffer).digest('hex');
  const staging_path = path.join(UPLOAD_DIR, `${file_hash}${ext}`);
  fs.writeFileSync(staging_path, buffer);

  return { staging_path, file_hash };
}

async function stageZip(
  buffer: Buffer
): Promise<{ staging_path: string; file_hash: string }> {

  // 1. Compressed size check
  if (buffer.length > MAX_ZIP_SIZE_BYTES) {
    throw new UploadValidationError(
      `ZIP file exceeds max compressed size (${MAX_ZIP_SIZE_BYTES / 1_048_576} MB)`
    );
  }

  const zip = new AdmZip(buffer);
  const entries = zip.getEntries();

  // 2. File count check
  if (entries.length > MAX_ZIP_FILE_COUNT) {
    throw new UploadValidationError(
      `ZIP contains ${entries.length} files; max is ${MAX_ZIP_FILE_COUNT}`
    );
  }

  // 3. Uncompressed size check BEFORE extractall() — stops ZIP bombs
  const totalUncompressed = entries.reduce((sum, e) => sum + e.header.size, 0);
  if (totalUncompressed > MAX_EXTRACTED_SIZE_BYTES) {
    throw new UploadValidationError(
      `ZIP would extract to ${(totalUncompressed / 1_048_576).toFixed(1)} MB; max is ${MAX_EXTRACTED_SIZE_BYTES / 1_048_576} MB`
    );
  }

  // 4. Path traversal check (ZIP slip) on every entry
  const extract_dir = path.join(UPLOAD_DIR, `zip_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`);
  fs.mkdirSync(extract_dir, { recursive: true });

  for (const entry of entries) {
    if (entry.isDirectory) continue;

    const member_path = path.resolve(extract_dir, entry.entryName);
    if (!member_path.startsWith(path.resolve(extract_dir))) {
      // Clean up before throwing
      fs.rmSync(extract_dir, { recursive: true, force: true });
      throw new UploadValidationError(`Unsafe path in ZIP archive: ${entry.entryName}`);
    }

    const ext = path.extname(entry.entryName).toLowerCase();
    if (!ALLOWED_EXTENSIONS.has(ext)) {
      logger.warn('Skipping unsupported file in ZIP', { entry: entry.entryName });
      continue;
    }

    // Extract single entry
    const entry_dir = path.dirname(member_path);
    fs.mkdirSync(entry_dir, { recursive: true });
    fs.writeFileSync(member_path, entry.getData());
  }

  const file_hash = crypto.createHash('sha256').update(buffer).digest('hex');
  return { staging_path: extract_dir, file_hash };
}

/**
 * Encrypt staged files and move them to permanent storage path.
 * Returns the encrypted output path.
 */
export function encryptAndStore(staging_path: string, job_id: string): string {
  const store_dir = path.join(UPLOAD_DIR, 'encrypted', job_id);
  fs.mkdirSync(store_dir, { recursive: true });

  const stats = fs.statSync(staging_path);

  if (stats.isDirectory()) {
    // Encrypt each file individually
    const files = fs.readdirSync(staging_path);
    for (const file of files) {
      const src = path.join(staging_path, file);
      if (!fs.statSync(src).isFile()) continue;
      const plaintext = fs.readFileSync(src);
      const ciphertext = encryptBuffer(plaintext);
      fs.writeFileSync(path.join(store_dir, `${file}.enc`), ciphertext);
    }
  } else {
    const plaintext = fs.readFileSync(staging_path);
    const ciphertext = encryptBuffer(plaintext);
    const enc_name = path.basename(staging_path) + '.enc';
    fs.writeFileSync(path.join(store_dir, enc_name), ciphertext);
  }

  // Remove plaintext staging copy
  fs.rmSync(staging_path, { recursive: true, force: true });

  return store_dir;
}

/**
 * Decrypt stored files into a temp working directory for a Docker worker.
 * Returns the temp directory path.
 */
export function decryptForWorker(store_dir: string, job_id: string): string {
  const { decryptBuffer } = require('./encryption');
  const work_dir = path.join(UPLOAD_DIR, 'work', job_id);
  fs.mkdirSync(work_dir, { recursive: true });

  const files = fs.readdirSync(store_dir);
  for (const file of files) {
    if (!file.endsWith('.enc')) continue;
    const ciphertext = fs.readFileSync(path.join(store_dir, file));
    const plaintext = decryptBuffer(ciphertext);
    const original_name = file.replace(/\.enc$/, '');
    fs.writeFileSync(path.join(work_dir, original_name), plaintext);
  }

  return work_dir;
}

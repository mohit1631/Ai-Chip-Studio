// =====================================================
// AI Chip Studio — Phase 6 — Shared Types
// =====================================================

export type UserRole = 'owner' | 'admin' | 'editor' | 'viewer';
export type SubscriptionTier = 'free' | 'pro' | 'enterprise';
export type JobStatus = 'pending' | 'active' | 'completed' | 'failed' | 'cancelled';
export type JobType =
  | 'lint'
  | 'bugdetect'
  | 'simulate'
  | 'coverage'
  | 'formal'
  | 'synthesis'
  | 'physical_design'
  | 'ai_copilot';

export type WorkerPool = 'light' | 'heavy';

// ---- Auth & Users ----

export interface JwtPayload {
  sub: string;       // user_id
  email: string;
  role: UserRole;    // global role (project-level RBAC is separate)
  tier: SubscriptionTier;
  iat: number;
  exp: number;
}

export interface UserRecord {
  id: string;
  email: string;
  password_hash: string;
  role: UserRole;
  tier: SubscriptionTier;
  created_at: Date;
  last_login_at: Date | null;
}

export interface ProjectMember {
  project_id: string;
  user_id: string;
  role: UserRole;
}

// ---- Projects ----

export interface ProjectRecord {
  id: string;
  name: string;
  owner_id: string;
  created_at: Date;
  encryption_key_id: string | null;  // for enterprise customer-managed keys
}

// ---- Jobs ----

export interface JobRecord {
  id: string;
  project_id: string;
  user_id: string;
  type: JobType;
  status: JobStatus;
  pool: WorkerPool;
  input_path: string;         // encrypted path in object storage
  output_path: string | null;
  error_message: string | null;
  created_at: Date;
  started_at: Date | null;
  completed_at: Date | null;
  docker_container_id: string | null;
}

// ---- BullMQ Job Payload ----

export interface JobPayload {
  job_id: string;
  project_id: string;
  user_id: string;
  type: JobType;
  input_path: string;          // local or S3 path to uploaded files
  options: Record<string, unknown>;
}

// ---- AI Service ----

export interface AIRequest {
  prompt: string;
  system_prompt: string;
  job_context: {
    job_id: string;
    project_id: string;
    type: JobType;
  };
  max_tokens?: number;
  temperature?: number;
}

export interface AIResponse {
  content: string;
  model_used: string;
  provider: 'ollama' | 'anthropic';
  latency_ms: number;
  fallback_triggered: boolean;
}

// ---- API Responses ----

export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  request_id: string;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  type: JobType;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  output_url: string | null;
  error_message: string | null;
}

// ---- Audit ----

export interface AuditLogEntry {
  id: string;
  user_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  ip_address: string;
  user_agent: string;
  created_at: Date;
  metadata: Record<string, unknown>;
}

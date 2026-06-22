/**
 * API TypeScript Interfaces
 * Synchronized with backend schemas in services/gateway/schemas.py
 */

export interface ServiceDetail {
  git_sha?: string;
  start_time?: number | null;
}

export interface HealthStatus {
  status: 'READY' | 'NOT_READY';
  services: Record<string, string>;
  service_details?: Record<string, ServiceDetail>;
}

export interface ServiceInfo {
  service: string;
  version: string;
  git_sha: string;
  git_branch: string;
  build_date: string;
}

export interface LogEntry {
  id?: number;
  timestamp: string;
  service: string;
  level: string;
  message: string;
  context?: Record<string, unknown> | null;
}

export interface Workspace {
  id: string;
  display_name: string;
  local_path: string;
  host_mount_path?: string | null;
  container_mount_path?: string | null;
  resolved_path?: string | null;
  available?: boolean;
  nextcloud_path?: string | null;
  repo_url?: string | null;
  git_remote?: string | null;
  default_branch?: string | null;
  sync_mode: string;
  scope: string;
  capabilities: string[];
  owner_user?: string | null;
  is_default?: boolean;
  auto_pull_enabled: boolean;
  auto_backup_enabled?: boolean;
  webhook_token?: string | null;
  quarantined?: boolean;
  last_raven_mission_id?: number | null;
  excludes?: string[];
}

export type WorkspaceListResponse =
  | Workspace[]
  | {
      status?: string;
      workspaces?: Workspace[];
    };

export interface UserProfileRaw {
  id: string | number;
  username: string;
  display_name?: string;
  full_name?: string;
  role?: 'admin' | 'user';
  is_admin?: boolean;
  is_system_default?: boolean;
  nextcloud_url?: string | null;
  nextcloud_user?: string | null;
  ha_url?: string | null;
  github_url?: string | null;
  github_user?: string | null;
  gitlab_url?: string | null;
  gitlab_user?: string | null;
  git_url?: string | null;
  git_user?: string | null;
 audiobookshelf_url?: string | null;
  audiobookshelf_user?: string | null;
  audiobookshelf_api_key?: string | null;
  skylight_url?: string | null;
  skylight_email?: string | null;
  skylight_enabled?: boolean;
  voice_fingerprint?: string | null;
  voice_id?: string | null;
  avatar_url?: string | null;
  share_with_all?: boolean;
  [key: string]: unknown;
}

export interface UserProfile extends UserProfileRaw {
  full_name?: string;
  role: 'admin' | 'user';
  is_admin: boolean;
  voice_id?: string | null;
}

export interface APIKey {
  id: string | number;
  label: string;
  prefix: string;
  created_at?: string;
  key?: string;
  owner_username?: string;
  owner_id?: number;
}

export interface DiscoveredUser {
  username: string;
  source: string;
  display_name?: string;
}

export interface DeviceAssignment {
  id: number;
  device_id: string;
  user_id: number;
  username: string;
}

export interface GlobalSetting {
  key: string;
  value: string;
  description?: string;
}

export interface GatewayConfig {
  assistant_model: string;
  coding_model: string;
  librarian_model: string;
}

export interface ExecutionResponse {
  status: 'SUCCESS' | 'FAILURE' | 'PARTIAL';
  message: string;
  service: string;
  detail?: Record<string, unknown> | null;
}

export interface TimerRecord {
  id: string;
  type: string;
  title: string;
  expires_at: string;
  active: boolean;
  recurrence?: string | null;
  target_device?: string | null;
}

export interface TalkConversation {
  id?: number;
  token: string;
  display_name: string;
  name?: string | null;
  description?: string | null;
  unread_messages?: number;
  last_activity?: number | null;
  last_message?: string | null;
}

export interface TalkMessage {
  id?: number;
  token: string;
  actor_type?: string | null;
  actor_id?: string | null;
  actor_display_name: string;
  timestamp?: number | null;
  message_type?: string | null;
  system_message?: string | null;
  message?: string | null;
  is_replyable?: boolean;
}

export interface SmokeTestResult {
  status: string;
  passed: boolean;
  results: string;
}

export interface StorageEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size?: number | null;
  mtime?: string | null;
  content_type?: string | null;
  indexed?: boolean;
}

export interface RagStats {
  total_chunks: number;
  total_documents: number;
  last_indexed?: string;
  providers?: string[];
  breakdown?: Record<string, { chunks: number; documents: number }>;
  status?: string;
  message?: string;
}

export interface RavenMission {
  id: number;
  mission_type: string;
  priority: number;
  target_container?: string | null;
  error_summary?: string | null;
  proposed_mission: string;
  coding_model?: string | null;
  status: string;
  progress: number;
  scheduled_for?: string | null;
  created_at: string;
  output_log?: string | null;
  result?: string | null;
  user_id?: number | null;
}

export interface RavenConfig {
  raven_suspended: boolean;
  raven_scan_interval: number;
  raven_error_threshold: number;
  active_coding_model: string | null;
  system_default_tts_voice: string;
  system_default_tts_engine: string;
  cleanup_interval_seconds?: number;
}

export interface MediaGroup {
  name: string;
  member_entity_ids?: string[];
}

export interface LightCluster {
  name: string;
  member_entity_ids?: string[];
}

export interface LightPattern {
  name: string;
  steps?: unknown[];
}

export interface TelemetryEnrollment {
  entity_id: string;
  power_tracking: boolean;
  availability_tracking: boolean;
  offline_alert_threshold_minutes: number;
}

export interface IntercomSessionData {
  session_id: string;
  caller_user_id: string;
  target_user_id?: string;
  target_room?: string;
  session_type: string;
  status: string;
}

export interface IntercomConfigData {
  default_tts_engine?: string;
  default_voice?: string;
  default_volume?: number;
  enable_espresense_routing?: boolean;
}

/**
 * ChatRequest maps directly to gateway/schemas.py:ChatRequest
 */
export interface ChatRequest {
  query: string;
  voice_id?: string | null;
  device_id?: string | null;
  rag_user?: string | null;
  model?: string | null;
  stream?: boolean;
  api_key?: string | null;
  client?: 'chat' | 'voice' | 'home_assistant';
  source?: string | null;
}

/**
 * ChatResponse maps directly to gateway/schemas.py:ChatResponse
 */
export interface ChatResponse {
  status: 'SUCCESS' | 'FAILURE';
  message: string;
  intent?: string | null;
  confidence?: number | null;
  llm_bypassed: boolean;
  execution_result?: Record<string, unknown> | null;
}

/**
 * AnnouncementRequest maps directly to gateway/schemas.py:AnnouncementRequest
 */
export interface AnnouncementRequest {
  entity_id: string;
  message: string;
  volume?: number;
  tts_engine?: 'kokoro' | 'piper';
  storybook?: boolean;
  save_path?: string | null;
}

/**
 * PatchChunk maps to gateway/schemas.py:PatchChunk
 */
export interface PatchChunk {
  target_content: string;
  replacement_content: string;
}

/**
 * WorkspaceFilePatchRequest maps to gateway/schemas.py:WorkspaceFilePatchRequest
 */
export interface WorkspaceFilePatchRequest {
  file_path: string;
  patch: PatchChunk[];
  commit_after?: boolean;
  commit_message?: string | null;
}

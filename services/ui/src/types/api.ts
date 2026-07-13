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

export interface SearchResult {
  answer?: string;
  files?: Array<{ name: string; path: string }>;
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
  events?: unknown[];
  settings?: {
    default?: string;
    disabled?: string[];
    priority?: Record<string, number>;
    ical_urls?: string[];
  } | null;
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
  queued_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration?: number | null;
  output_log?: string | null;
  result?: string | null;
  user_id?: number | null;
  workspace_id?: string | null;
  last_llm_reply?: string | null;
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
  defaultVoice?: string;
  default_volume?: number;
  enable_espresense_routing?: boolean;
}

export interface ServiceStatus {
  name: string;
  status: string;
  image: string;
  image_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number;
  uptime_seconds?: number | null;
  uptime?: string | null;
  health?: string | null;
  health_status?: string | null;
  pid?: number | null;
  restart_count?: number;
  image_pull_time?: string | null;
  memory_usage?: number | null;
}

export interface ImagePullResult {
  service: string;
  image: string;
  current_image_id: string;
  latest_image_id: string;
  updated: boolean;
  message: string;
}

export interface ImageUpdateCheck {
  service: string;
  image: string;
  /** Local image RepoDigest (sha256:...) — may be null if image was built locally */
  current_digest: string | null;
  /** Remote manifest digest fetched from registry — null if auth failed or unreachable */
  remote_digest: string | null;
  has_update: boolean;
  /** Set when the comparison could not be completed (e.g. no_image_tag, digest_unavailable) */
  check_error?: string | null;
  status: string;
}

export interface CheckUpdatesResponse {
  checked: number;
  updates_available: number;
  services: ImageUpdateCheck[];
}

export interface SystemHealthStatus {
  total_services: number;
  running: number;
  stopped: number;
  unhealthy: number;
  control_plane: {
    status: string;
    git_sha: string;
    start_time: number;
    uptime: string;
  };
  services: ServiceStatus[];
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

export interface TelemetryDataPoint {
  recorded_at: number;
  power_w?: number;
  is_available?: boolean;
  state?: string;
  source?: string;
}

export interface TelemetrySummary {
  entity_id: string;
  summary: {
    current_power_w: number | null;
    peak_power_w: number | null;
    avg_power_w: number | null;
    availability_pct: number;
    total_activations: number;
    data_points: TelemetryDataPoint[];
  } | null;
}

export interface TelemetryDataResponse {
  entity_id: string;
  data: TelemetryDataPoint[];
}

export interface TelemetryInsights {
  entity_id: string;
  insights: Array<{
    type: string;
    message: string;
    severity: 'info' | 'warning' | 'critical';
    timestamp: number;
  }>;
}

export interface ModelInfo {
  name: string;
  size: number;
  digest: string;
  modified_at: string;
  details: {
    format: string;
    family: string;
    families: string[];
    parameter_size: string;
    quantization_level: string;
  };
}

export interface ModelsResponse {
  models: ModelInfo[];
}

export interface ModelSwitchRequest {
  model_name: string;
}

export interface ModelSwitchResponse {
  status: string;
  message: string;
}

export interface GenerateRequest {
  model: string;
  prompt: string;
  stream?: boolean;
  options?: Record<string, unknown>;
}

export interface GenerateResponse {
  response: string;
  done: boolean;
  context?: number[];
  total_duration?: number;
  load_duration?: number;
  prompt_eval_count?: number;
  eval_count?: number;
  eval_duration?: number;
}

export interface EmbeddingsRequest {
  model: string;
  input: string | string[];
}

export interface EmbeddingsResponse {
  embeddings: number[][];
}

export interface TagsResponse {
  models: Array<{
    name: string;
    size: number;
    digest: string;
    modified_at: string;
  }>;
}

export interface ShowRequest {
  name: string;
  verbose?: boolean;
}

export interface WorkspaceFileEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size?: number;
  mtime?: string;
  content?: string;
}

export interface WorkspaceFilesListResponse {
  files: WorkspaceFileEntry[];
}

export interface WorkspaceFileListResponse {
  status: string;
  relative_path: string;
  resolved_path?: string;
  entries: WorkspaceFileEntry[];
  truncated: boolean;
}

export interface GitStatusResponse {
  status: string;
  branch?: string;
  upstream?: string | null;
  porcelain: string[];
  dirty: boolean;
}

export interface GitDiffResponse {
  status: string;
  diff: string;
}

export interface GitCommitResponse {
  status: string;
  message?: string;
}

export interface GitPushResponse {
  status: string;
  message?: string;
}

export interface GitLogEntry {
  commit: string;
  message: string;
  author?: string;
}

export interface GitLogResponse {
  status: string;
  entries: GitLogEntry[];
}

export interface StorageMirrorResponse {
  status: string;
  message?: string;
}

export interface WorkspaceFileReadResponse {
  content: string;
  path: string;
}

export interface WorkspaceFileWriteRequest {
  path: string;
  content: string;
}

export interface WorkspaceFileWriteResponse {
  status: string;
  message: string;
}

export interface PytestRequest {
  workspace_id: string;
  test_path?: string;
  args?: string[];
}

export interface PytestResponse {
  status: string;
  output: string;
  passed: boolean;
}

export interface WorkflowWriteSyncCommitRequest {
  workspace_id: string;
  branch?: string;
  message?: string;
}

export interface VolumeInfo {
  name: string;
  driver: string;
  mountpoint: string;
  labels: Record<string, string>;
  scope: string;
}

export interface VolumesResponse {
  volumes: VolumeInfo[];
}

export interface ServiceLogsResponse {
  logs: string;
  service: string;
}

export interface ContainerExecRequest {
  cmd: string[];
  env?: Record<string, string>;
}

export interface ContainerExecResponse {
  output: string;
  exit_code: number;
}

export interface RagIndexedPathsResponse {
  paths: string[];
}

export interface RagSyncFilesRequest {
  paths: string[];
  recursive?: boolean;
}

export interface RagSyncCapabilitiesRequest {
  force?: boolean;
}

export interface WorkspaceShellRequest {
  workspace_id: string;
  command: string;
  cwd?: string;
}

export interface WorkspaceShellResponse {
  output: string;
  exit_code: number;
}

export interface WorkspaceFilePatchExecuteRequest {
  workspace_id: string;
  file_path: string;
  patch: PatchChunk[];
  commit_after?: boolean;
  commit_message?: string | null;
}

export interface WorkspaceLintRequest {
  workspace_id: string;
  path?: string;
  fix?: boolean;
}

export interface WorkspaceLintResponse {
  status: string;
  output: string;
}

export interface VolumesExecuteRequest {
  action: string;
  volume_name?: string;
  options?: Record<string, unknown>;
}

export interface DiscoveryProfileResponse {
  entity_id: string;
  profile: Record<string, unknown>;
}

export interface NetworkScanRequest {
  subnet?: string;
}

export interface NetworkScanResponse {
  hosts: Array<{
    ip: string;
    hostname?: string;
    mac?: string;
    vendor?: string;
    open_ports: number[];
  }>;
}

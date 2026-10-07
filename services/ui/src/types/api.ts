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
  created_at?: string | null;
  /** Identity user whose Nextcloud account backs the sync (set on first sync). */
  sync_owner?: string | null;
  last_sync_at?: string | null;
  last_sync_status?: 'ok' | 'conflicts' | 'error' | null;
  last_sync_error?: string | null;
}

/** Where a workspace's files live. "git" is a legacy alias of local_git_authoritative. */
export type WorkspaceSyncMode = 'local_git_authoritative' | 'git' | 'nextcloud' | 'git_and_nextcloud';
export type WorkspaceSyncDirection = 'both' | 'push' | 'pull';

export interface WorkspaceSyncResult {
  direction: WorkspaceSyncDirection;
  remote_root: string;
  dry_run: boolean;
  uploaded: string[];
  downloaded: string[];
  deleted_local: string[];
  deleted_remote: string[];
  created_local_dirs: string[];
  created_remote_dirs: string[];
  conflicts: { path: string; remote_copy: string }[];
  errors: { path: string; error: string }[];
  bytes_uploaded: number;
  bytes_downloaded: number;
  changed: boolean;
}

export interface WorkspaceSyncResponse {
  status: string;
  workspace_id: string;
  result: WorkspaceSyncResult;
}

export interface WorkspaceUploadResponse {
  status: 'SUCCESS' | 'PARTIAL';
  workspace_id: string;
  relative_path: string;
  uploaded: { relative_path: string; size: number; sha256: string; created: boolean }[];
  skipped: { relative_path: string; skipped: true; reason: string }[];
  created_dirs: string[];
  errors: { relative_path: string; error: string }[];
  bytes_written: number;
}

export type WorkspaceListResponse =
  | Workspace[]
  | {
      status?: string;
      workspaces?: Workspace[];
    };

export interface UserProfileRaw {
  mail_user?: string;
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
  mass_url?: string | null;
  mass_token?: string | null;
  skylight_url?: string | null;
  skylight_email?: string | null;
  skylight_enabled?: boolean;
  voice_fingerprint?: string | null;
  voice_id?: string | null;
  avatar_url?: string | null;
  [key: string]: unknown;
}

/**
 * Services a user may borrow from the system default user when they have no
 * credentials of their own. Grants are per service, admin-only and revocable
 * (see docs/PER_USER_CREDENTIALS.md).
 */
export type ShareableService = 'home_assistant' | 'music_assistant' | 'audiobookshelf' | 'nextcloud';

export interface CredentialShares {
  username: string;
  services: ShareableService[];
  granted_by?: string | null;
  granted_at?: string | null;
  note?: string | null;
  shared_owner?: string | null;
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
  email?: string;
  ha_person_id?: string;
  nc_username?: string;
  abs_username?: string;
  mail_address?: string;
}

export interface DeviceAssignment {
  id: number;
  device_id: string;
  user_id: number;
  username: string;
}

/**
 * An account an activity-sharing grant may name.
 *
 * Deliberately narrower than {@link UserProfile}: this is what a non-admin
 * needs to build the share picker, and it carries no integration URLs,
 * credential fields, voice fingerprint or API key.
 */
export interface ShareRecipient {
  username: string;
  display_name: string;
}

/**
 * An entity locked against normal users. While a row exists, the entity's
 * DeviceAssignment is ignored: only admins, the system default user, and the
 * names in `permitted_usernames` may see or control it. Releasing the lock
 * deletes the row, so an entity is never "protected but inactive".
 */
export interface EntityProtection {
  entity_id: string;
  permitted_usernames: string[];
  granted_by: string;
  granted_at: string;
  note?: string | null;
}

export interface GlobalSetting {
  key: string;
  value: string;
  description?: string;
}

/** A device linked to a user: phones register themselves on login; watches,
 * assistants and lights are added through pairing (code) or adoption. */
/** One battery report from a device. pct is absent while it is on USB. */
export interface BatteryReading {
  at: string;
  pct?: number | null;
  usb?: boolean | null;
  cell_v?: number | null;
}

export interface CompanionDevice {
  device_key: string;
  kind: string; // phone | watch | assistant | light
  label: string;
  owner_username?: string | null;
  registered_by: string; // self | admin | paired | adopted
  model?: string | null;
  manufacturer?: string | null;
  os_version?: string | null;
  app_version?: string | null;
  app_build?: string | null;
  esphome_version?: string | null;
  hardware?: string | null;
  /** What the device told the server it can do; may be empty. */
  capabilities?: Record<string, unknown>;
  last_ip_address?: string | null;
  last_seen_at?: string | null;
  first_seen_at?: string | null;
  battery?: BatteryReading | null;
}

/**
 * One usage event a device reported, with whatever small scalars it carried.
 *
 * Every event comes from the no-opt-in allowlist, so this is activity — an app
 * open, a battery reading, a step count — and never anything the device heard
 * or saw.
 */
export interface DeviceEventRead {
  event: string;
  at: string;
  extra: Record<string, unknown>;
}

/** What one device has reported lately, for its detail view. */
export interface DeviceActivity {
  device: CompanionDevice;
  /** How many of each event arrived inside the window. */
  counts: Record<string, number>;
  /** The most recent events, newest first. */
  events: DeviceEventRead[];
  first_seen_at?: string | null;
  last_seen_at?: string | null;
}

/**
 * Per-device step history: {source: {day: steps}}.
 *
 * A fused daily total cannot say which device produced it. A source with
 * nothing recorded is **absent** rather than zero, because no report and a
 * reported zero are different facts.
 */
export interface StepSources {
  user_id: string;
  days: number;
  sources: Record<string, Record<string, number>>;
  hourly: Record<string, Record<string, number>>;
  last_synced?: number | null;
}

export interface PairDeviceRequest {
  step: 'discover' | 'start' | 'finish';
  host?: string;
  port?: number;
  code?: string;
  kind?: string;
}

export interface DiscoveredDevice {
  name: string;
  friendly_name: string;
  host: string;
  port: number;
  mac?: string | null;
}

export interface EsphomeDevice {
  name: string;
  host: string;
  port?: number;
  noise_psk?: string;
  /** Optional HA entity (e.g. light.office_light) this device mirrors for unified routing */
  ha_entity_id?: string;
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

export interface ArcadeGameCard {
  slug: string;
  title: string;
  category?: string;
  plays?: number;
  top_score?: number | null;
  score_count?: number;
  benchmark_score?: number | null;
  rating?: { count: number; average: number };
  featured?: boolean;
}

export interface ArcadeGamesResponse {
  success: boolean;
  arcade_available: boolean;
  error?: string;
  play_base: string;
  featured_source: 'admin' | 'rating' | 'benchmark' | 'none';
  featured: ArcadeGameCard[];
  games: ArcadeGameCard[];
  count: number;
}

export interface TimerRecord {
  id: string;
  type: string;
  title: string;
  expires_at: string;
  active: boolean;
  recurrence?: string | null;
  target_device?: string | null;
  duration_sec?: number;
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

/**
 * One storage crawl as reported by `GET /api/storage/status`.
 *
 * A crawl runs as a background task behind a 202, so this record is the only
 * honest way to show progress: `phase` walks listing → extracting → syncing,
 * `done`/`total` count the current phase, and `error` records why a crawl
 * stopped if it failed rather than leaving a spinner running forever.
 */
export interface StorageCrawl {
  active: boolean;
  kind?: string;
  path?: string;
  phase?: string;
  done?: number;
  total?: number;
  started_at?: number;
  updated_at?: number;
  files?: number;
  chunks?: number;
  synced?: number;
  error?: string;
}

/** Storage indexer health plus the active crawl, if any. */
export interface StorageStatus {
  status: string;
  indexer: string;
  checkpointed_files: number;
  message?: string;
  crawl?: StorageCrawl;
  rag_index?: Record<string, unknown>;
}

/**
 * Which of the three workspace-composer jobs to run.
 *
 * `auto` lets the gateway decide from the shape of the query and reports back
 * which it chose, so the caller can show the user what actually ran.
 */
export type WorkspaceAskMode = 'auto' | 'librarian' | 'single_task' | 'raven';

/** The three settled jobs, as opposed to `auto` which has not been resolved yet. */
export type ResolvedWorkspaceAskMode = Exclude<WorkspaceAskMode, 'auto'>;

/**
 * Result of one workspace composer turn.
 *
 * The two synchronous modes answer inline and carry `model`; `raven` dispatches
 * a mission instead and carries `mission_id`. `reason` explains why the
 * resolved mode was chosen, which is the only way the user can tell an
 * automatic decision from a deliberate one.
 */
export interface WorkspaceAskResult {
  status: string;
  requested_mode: WorkspaceAskMode;
  resolved_mode: ResolvedWorkspaceAskMode;
  reason: string;
  context_chars: number;
  model?: string;
  answer?: string;
  mission_id?: number | null;
  mission?: RavenMission;
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
  raven_max_total_seconds?: number;
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

/** Mirrors TelemetryEnrollment in services/execution/schemas_telemetry.py. */
export interface TelemetryEnrollment {
  entity_id: string;
  power_tracking: boolean;
  availability_tracking: boolean;
  usage_tracking: boolean;
  offline_alert_threshold_minutes: number;
  group_id?: string | null;
  power_attribute?: string | null;
  enrolled_at?: string;
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
  latest_image_id?: string;
  updated?: boolean;
  status?: string;
  message: string;
  pull_status?: PullStatus;
}

export interface PullStatus {
  service: string;
  status: 'idle' | 'pulling' | 'completed' | 'failed';
  progress: string;
  image?: string;
  current_image_id?: string;
  new_image_id?: string;
  updated?: boolean;
  started_at?: number;
  completed_at?: number | null;
  last_update?: number;
  error?: string | null;
}

export interface PullAndRestartResult {
  service: string;
  status: string;
  message: string;
  updated: boolean;
  current_image_id: string;
  new_image_id: string;
  volumes_fixed?: string[];
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
    peak_at: number | null;
    peak_duration_seconds: number | null;
    avg_power_w: number | null;
    availability_pct: number;
    total_activations: number;
    last_outage_at: number | null;
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

/**
 * Whether an AI tool can actually run, as opposed to merely being installed.
 * `available: null` is a real third state: the backend cannot be judged without
 * attempting a real request (a two-image swap, say), so the honest answer is
 * "unconfirmed" and the UI must not present it as either yes or no.
 */
export interface AiCapability {
  key: string;
  label: string;
  available: boolean | null;
  detail: string;
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
  is_git_repo?: boolean;
  branch?: string;
  upstream?: string | null;
  porcelain: string[];
  dirty: boolean;
}

export interface GitBranchesResponse {
  status: string;
  is_git_repo?: boolean;
  current?: string;
  local: string[];
  remote: string[];
}

export interface GitCheckoutResponse {
  status: string;
  branch?: string;
  current_branch?: string;
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
  is_git_repo?: boolean;
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

/**
 * A trip endpoint as stored by the geo service
 * (`{"name", "latitude", "longitude"}` — services/geo/main.py).
 * `lat`/`lon`/`zone` are the legacy spelling, still tolerated on read.
 */
export interface TripLocation {
  name?: string;
  address?: string;
  latitude?: number;
  longitude?: number;
  lat?: number;
  lon?: number;
  zone?: string;
}

export interface ResolvedTripLocation {
  name: string;
  lat: number | null;
  lon: number | null;
  source: 'ha_zone' | 'osm' | 'coords' | 'stored' | null;
}

export interface TripLocationsResponse {
  trip_id: string;
  start: ResolvedTripLocation;
  end: ResolvedTripLocation;
}

export interface TripLocationSuggestion {
  name: string;
  kind: string;
  distance_m?: number;
  address?: string;
}

export interface LocationSuggestionsResponse {
  status: string;
  latitude: number;
  longitude: number;
  current: { name: string; address?: string | null; source: string };
  candidates: TripLocationSuggestion[];
}

export interface Trip {
  id: string;
  user_id: string;
  user_name: string;
  vehicle_id?: string;
  vehicle_name?: string;
  fuel_type?: string;
  mpg: number;
  cost_per_gallon: number;
  start_time: number;
  end_time: number;
  duration_seconds: number;
  distance_miles: number;
  top_speed_mph: number | null;  // null when it could not be measured (e.g. a recomputed trip)
  avg_speed_mph?: number;
  start_location?: TripLocation;
  end_location?: TripLocation;
  fuel_used_gal: number;
  trip_cost_usd: number;
  activity_type: string;
  notes?: string | null;
  status: 'completed' | 'in_progress';
  created_at?: number;
  updated_at?: number;
  updated_by?: string | null;
  is_shared?: boolean;
  shared_with?: Array<{ user_id: string; user_name: string }>;
  shared_group_id?: string;
}

export interface TripUpdatePayload {
  vehicle_id?: string;
  vehicle_name?: string;
  fuel_type?: string;
  mpg?: number;
  cost_per_gallon?: number;
  activity_type?: string;
  notes?: string;
  start_name?: string;
  end_name?: string;
  start_address?: string;
  end_address?: string;
}

export interface TripsResponse {
  trips: Trip[];
  total_trips: number;
}

export interface RoutePoint {
  t: number;
  lat: number;
  lon: number;
  acc?: number | null;
  spd?: number | null;
  brg?: number | null;
  bat?: number | null;
}

export interface TripRouteResponse {
  trip_id?: string;
  workout_id?: string;
  points: RoutePoint[];
}

export interface Workout {
  id: string;
  user_id: string;
  activity_type: string;
  start_time: number;
  end_time?: number;
  duration_seconds?: number;
  distance_miles?: number;
  avg_speed_mph?: number | null;
  top_speed_mph?: number | null;
  steps?: number | null;
  steps_source?: 'pedometer' | 'gps_estimate' | null;
  notes?: string | null;
  /**
   * Geo writes `in_progress` for a running session and `completed` once it is
   * filed into history -- there is no `active` value, which is what made the
   * old client-side scan for one impossible to match.
   */
  status: 'in_progress' | 'completed';
}

export interface WorkoutsResponse {
  workouts: Workout[];
  total?: number;
}

export interface StepsResponse {
  user_id: string;
  /** Present on the wire (geo echoes the requested window). */
  days?: number;
  daily_steps: Record<string, number>;
  today: number;
  /**
   * Omitted by geo's no-redis early return, so treat it as optional even though
   * the happy path always sends it.
   */
  goal?: number;
  /**
   * Per-device contribution to *today*, e.g. `{ phone: 368 }`. Present so the
   * UI can explain a total that no single device counted.
   */
  sources?: Record<string, number>;
  /**
   * Epoch seconds of the phone's last successful upload, or null if it has
   * never synced. Added because without it a frozen number is
   * indistinguishable from a live one.
   */
  last_synced?: number | null;
}

/**
 * Ranges the step-history endpoint understands. Mirrors the server's
 * `step_history.RANGE_DAYS`; the server 422s on anything else.
 */
export type StepRange = 'D' | 'W' | 'M' | '3M' | 'Y';

export interface StepRangeBucket {
  label: string;
  /** ISO date, inclusive. */
  start: string;
  end: string;
  steps: number;
  /**
   * Days in this window with no reading at all. Kept apart from `steps` so a
   * gap is never drawn as a genuine zero.
   */
  days_missing: number;
  days_recorded: number;
  complete: boolean;
}

export interface StepRangeResponse {
  user_id: string;
  range: StepRange;
  label: string;
  buckets: StepRangeBucket[];
  total: number;
  daily_average: number;
  days_recorded: number;
  goal: number | null;
  /**
   * The user's own median daily steps, or null when there is not enough
   * history for it to mean anything. Never a population average.
   */
  baseline: number | null;
  /** How many days a baseline needs before it is worth comparing against. */
  baseline_min_days: number;
  /** True when the window is too short for `baseline` to be meaningful. */
  thin: boolean;
  has_gaps: boolean;
  /** Null when no day in the window was actually measured. */
  best: { label: string; steps: number } | null;
  /**
   * Hour-by-hour breakdown, sent only for `range: 'D'` and only once the
   * phone has actually reported hours. Deliberately optional rather than an
   * empty array: an unrecorded day must not look like 24 hours of zero.
   */
  hourly?: StepHourBucket[];
  /** The busiest recorded hour of today, or absent along with `hourly`. */
  peak?: StepHourBucket;
}

export interface StepHourBucket {
  /** 0-23, the phone's own local hour. */
  hour: number;
  /** Server-formatted `HH:00` label. */
  label: string;
  steps: number;
}

/**
 * One bucket of an event metric's history (workouts, distance, minutes).
 *
 * Deliberately has no `days_missing`/`has_gaps`: a day with no workout is a
 * real zero, unlike a day a pedometer did not report. Absence of data and a
 * zero are different facts for steps and the same fact here, so the type
 * itself does not let them be confused.
 */
export interface MetricRangeBucket {
  label: string;
  start: string;
  end: string;
  value: number;
  /** Days in this bucket where something actually happened. */
  active_days: number;
  quiet: boolean;
}

export interface MetricRangeResponse {
  user_id: string;
  metric: string;
  label: string;
  unit: string;
  format: string;
  range: StepRange;
  range_label: string;
  buckets: MetricRangeBucket[];
  total: number;
  /** Average across days that were active, not across the calendar. */
  per_active_day: number;
  active_days: number;
  /** True when nothing at all happened in the window. */
  empty: boolean;
  best: { label: string; value: number } | null;
}

/** One entry in the personal event timeline (a workout, an earned badge). */
export interface TimelineEvent {
  kind: 'workout' | 'achievement' | 'goal' | 'drive' | 'personal_best';
  at: number;
  title: string;
  detail: string;
  meta: Record<string, unknown>;
  days_ago: number;
  /** The kind's name ("Workout", "Drive") -- a fallback if no time is known. */
  label: string;
  /**
   * Time of day ("8:05 AM") as the server computed it, in the zone it used to
   * bucket the day. Must be shown rather than formatting `at` locally: the
   * device's own zone could file the event on a different day than the group
   * it is listed under. "" when unknown.
   */
  time_label: string;
  icon: string;
}

export interface TimelineDay {
  day: string;
  relative: string;
  events: TimelineEvent[];
}

export interface TimelineResponse {
  user_id: string;
  /** The window that was requested. */
  window_days: number;
  /** How many day groups came back — NOT the same as window_days. */
  day_count: number;
  groups: TimelineDay[];
  total_events: number;
  /** Explicit, so the UI says "nothing recorded yet" rather than showing a blank list. */
  empty: boolean;
}

export interface MetricCatalogResponse {
  available: string[];
  /**
   * Metrics a person would expect to see, with the reason each is absent.
   *
   * Surfacing the reason is the point: calories looks like a missing feature
   * when it is actually a field nothing ever writes.
   */
  unavailable: Record<string, string>;
}

export interface ActivityTrendsResponse {
  user_id: string;
  days: number;
  steps_today: number | null;
  steps_goal: number;
  daily_steps: Record<string, number>;
  steps_avg: number | null;
  steps_average?: number | null;
  steps_best: { date: string; steps: number } | null;
  workout_count: number;
  workouts_by_type: Record<string, number>;
  workout_distance_miles: number;
  trip_count: number;
  trip_distance_miles: number;
  drive_fuel_gallons: number;
  drive_cost_usd: number;
  analysis: string | null;
  analysis_available: boolean;
  generated_at: number;
  cached?: boolean;
}

export type ActivityWindow = 'today' | 'week' | 'month';

/** One person's opt-in shared activity — only the scopes they chose appear. */
export interface SharedActivityUser {
  username: string;
  window: string;
  steps_total?: number;
  steps_average?: number;
  steps_today?: number;
  workout_count?: number;
  workout_distance_miles?: number;
  drive_distance_miles?: number;
  points?: number;
  achievements_earned?: number;
}

export interface ActivityFeedResponse {
  status: string;
  viewer: string;
  window: string;
  users: SharedActivityUser[];
}

export interface ActivitySummaryResponse extends Omit<SharedActivityUser, 'username'> {
  status: string;
  user_id: string;
  days: number;
}

/** A user's last known GPS fix as stored by Identity. */
export interface StarGrant {
  stars: number;
  balance: number;
  reason: string;
  note?: string;
  granted_by: string;
  at: number;
}

export interface StarsResponse {
  user_id: string;
  stars: number;
  grants: StarGrant[];
}

/**
 * The Skylight half of an admin grant.
 *
 * Reported separately from the ledger write on purpose: the ledger is the source
 * of truth and a mirror failure must not undo a grant, so the UI says "recorded,
 * mirror failed" rather than pretending either both or neither happened.
 */
export interface StarMirrorOutcome {
  status: 'SUCCESS' | 'FAILURE' | 'SKIPPED';
  message?: string;
}

export interface AdminStarGrantResponse {
  user_id: string;
  ledger: StarGrant;
  skylight: StarMirrorOutcome;
}

export interface ActivityGoals {
  daily_steps: number;
  weekly_steps: number;
  workouts_per_week: number;
  weekly_distance_miles: number;
}

export interface EarnedAchievement {
  id: string;
  name: string;
  description: string;
  points: number;
  earned_on: string;
}

export interface NextUpAchievement {
  id: string;
  name: string;
  description: string;
  points: number;
  current: number;
  target: number;
  remaining: number;
  percent: number;
}

export interface AchievementsResponse {
  user_id: string;
  earned: EarnedAchievement[];
  next_up: NextUpAchievement[];
  points: number;
  goals: ActivityGoals;
}
/** GET /api/geo/eta: drive time by road (OSRM) from a person's latest fix. */
export interface EtaResponse {
  user_id: string;
  to: string;
  arrived: boolean;
  duration_s: number;
  distance_m: number;
  /** Epoch seconds. */
  eta: number;
  moving: boolean;
  fix_age_s: number;
}

export interface UserLiveLocation {
  latitude: number;
  longitude: number;
  accuracy?: number | null;
  speed?: number | null;
  bearing?: number | null;
  battery?: number | null;
  timestamp?: number;
  updated_at?: number;
}

export type TelemetryReportType = 'health' | 'power';export type TelemetryReportPeriod = 'daily' | 'weekly' | 'monthly' | 'yearly';

export interface TelemetrySchedule {
  id: string;
  user: string;
  type: TelemetryReportType;
  period: TelemetryReportPeriod;
  run_at: string;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  attempts: number;
}

export interface TelemetryReport {
  id: string;
  user: string;
  type: TelemetryReportType;
  period: TelemetryReportPeriod;
  status: 'ready' | 'no_data' | string;
  analysis: string | null;
  stats: Record<string, unknown>;
  window_start?: string;
  window_end?: string;
  generated_at: string;
}

export interface TelemetryNotification {
  id: string;
  kind: 'report_ready' | 'report_failed' | string;
  report_type?: TelemetryReportType;
  period?: TelemetryReportPeriod;
  report_id?: string;
  title: string;
  error?: string;
  created_at: string;
  read: boolean;
}


// ── Bible (services/bible) ───────────────────────────────────────────────────

export interface BibleVersionInfo {
  code: string;
  name: string;
  language: string;
  license_class: 'public_domain' | 'licensed' | string;
  /** Who owns the text, for the copyrighted translations. */
  rights_holder: string;
  /** False when we know the translation but its text is not on this server. */
  installed: boolean;
  verse_count: number | null;
  imported_at: string | null;
  /** How many study Bibles explain this translation on this server. */
  editions: number;
  /**
   * True for the translation the reader gets when nothing is chosen. The
   * manifest marks exactly one, so the default is never whichever
   * translation happens to sort first.
   */
  primary: boolean;
  /**
   * The online provider that can install this translation ("api.bible"), or
   * empty when its text has to be supplied as a file.
   */
  provider: string;
  /**
   * Why an uninstalled translation is absent, or the do-not-redistribute
   * warning for an installed copyrighted one. Empty when there is nothing
   * to say.
   */
  note: string;
}

export interface BibleBookInfo {
  osis: string;
  name: string;
  order: number;
  chapters: number;
}

export interface BibleVerse {
  version: string;
  osis: string;
  book_name: string;
  chapter: number;
  verse: number;
  reference: string;
  text: string;
}

export interface BiblePassageSpan {
  book: string;
  book_name: string;
  chapter_start: number;
  chapter_end: number;
  verse_start: number;
  verse_end: number | null;
  whole_book: boolean;
  display: string;
}

export interface BiblePassage {
  version: string;
  requested: string;
  reference: string;
  spans: BiblePassageSpan[];
  verses: BibleVerse[];
  count: number;
}

export interface BibleSearchHit {
  osis: string;
  book_name: string;
  chapter: number;
  verse: number;
  reference: string;
  text: string;
}

export interface BibleSearchResult {
  version: string;
  query: string;
  results: BibleSearchHit[];
  count: number;
}

export interface BibleVerseOfDay {
  day: string;
  day_of_year: number;
  version: string;
  scope: 'all' | 'ot' | 'nt';
  osis: string;
  book: string;
  book_name: string;
  chapter: number;
  verse: number;
  reference: string;
  text: string;
}

/**
 * A devotional day. `kind` is `link` for sources we deep-link to (Blue Letter
 * Bible) and `text` for ones we serve ourselves; `skipped` names every source
 * that could not answer and the setting to fix, so an empty devotional is never
 * silent.
 */
export interface BibleDevotionalEntry {
  source: string;
  work: string;
  title: string;
  day_of_year: number;
  kind: 'text' | 'link';
  reference: string;
  text: string;
  url: string;
}

export interface BibleDevotionalSourceInfo {
  code: string;
  title: string;
  priority: number;
  works: string[];
  configured: boolean;
  reason: string;
}

export interface BibleDevotionalResponse {
  day?: string;
  day_of_year?: number;
  source: string;
  entry: BibleDevotionalEntry | null;
  skipped: Array<{ source: string; reason: string }>;
  reason?: string;
  stored?: boolean;
}

export interface BibleMark {
  id: number;
  version_code: string;
  osis: string;
  book_name: string;
  chapter: number;
  verse_start: number;
  verse_end: number | null;
  kind: 'highlight' | 'bookmark';
  color: string;
  note_path: string;
  note_preview: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface BiblePosition {
  book: string;
  chapter: number;
  verse: number;
}

export interface BiblePreferences {
  default_version: string;
  /** Which study Bible explains the text. Empty until one is chosen. */
  default_edition: string;
  /**
   * The reader's own favourite translation, kept apart from the default so
   * "resume where I left off" and "open my favourite" stay two decisions.
   * Empty until one is chosen.
   */
  favorite_version: string;
  /**
   * Which translation is shown beside the one being read. Empty means no
   * comparison; it is never guessed, because quietly showing a second version
   * the reader did not ask for would make it unclear which words they are
   * reading. A translation identical to the default is refused by the server.
   */
  compare_version: string;
  /**
   * Whether study panels may include commentary written for a different
   * translation. Off by default; the reader opts in per session or remembers it.
   */
  cross_version_notes: boolean;
  /**
   * Whether the chapter's commentary sits beside the text while reading, rather
   * than waiting behind a tap. Off by default so the reader starts as text.
   */
  show_notes: boolean;
  font_scale: number;
  line_height: number;
  theme: 'serif' | 'sans';
  read_aloud_voice: string;
  split_view: 'compare' | 'parallel';
}

export interface BibleStateResponse {
  username: string;
  position: BiblePosition | null;
  preferences: BiblePreferences;
}

export interface BibleStreaks {
  username: string;
  read_streak_current: number;
  read_streak_longest: number;
  open_streak_current: number;
  open_streak_longest: number;
  days_read: number;
}

export interface BibleStats extends BibleStreaks {
  metrics: Record<string, number>;
  days_opened: number;
  marks: number;
  highlights: number;
  bookmarks: number;
  position: BiblePosition | null;
  last_read_at: string | null;
  window: Record<string, number>;
}

export interface BibleEarnedAchievement {
  id: string;
  name: string;
  description: string;
  points: number;
  earned_on: string;
}

export interface BibleNextAchievement {
  id: string;
  name: string;
  description: string;
  points: number;
  current: number;
  target: number;
  remaining: number;
  percent: number;
  unit: string;
}

export interface BibleAchievementsResponse {
  username: string;
  points: number;
  newly_earned: BibleEarnedAchievement[];
  earned: BibleEarnedAchievement[];
  next_up: BibleNextAchievement[];
  stars: { granted: number; status: string };
  /** Rules whose backing feature lands in a later phase; they read zero today. */
  pending_rules: string[];
  announced: { posted: number; status: string };
}

export interface BibleActivitySummary {
  username: string;
  days_read: number;
  chapters_read: number;
  books_read: number;
  read_streak_current: number;
  read_streak_longest: number;
  achievements: Array<{ id: string; name: string }>;
  points: number;
}

export interface BibleActivityFeed {
  entries: BibleActivitySummary[];
  members: string[];
  count?: number;
  note?: string;
}

export interface BibleBlbLink {
  ref: string;
  url: string;
  tool: string | null;
}

/**
 * An answer about the passage being read.
 *
 * The model is given the verses on screen and the study notes stored for them
 * and nothing else, so the answer can be checked against what the reader can
 * see. A question the passage does not answer comes back saying so rather than
 * guessing -- the counts say how much material the answer had to work with.
 */
export interface BibleStudyAnswer {
  reference: string;
  version: string;
  edition: string;
  edition_name: string;
  question: string;
  answer: string;
  verses_used: number;
  notes_used: number;
}

/**
 * Study apparatus for a passage, kept out of the verse text itself.
 *
 * A study Bible carries commentary, footnotes and introductions next to the
 * scripture; we store them apart and load them only when asked, so the reading
 * surface stays plain. `ordinal` keeps several notes of one kind in the order
 * the publisher put them.
 */
export interface BibleStudyNote {
  osis: string;
  book: string;
  chapter: number;
  verse: number;
  reference: string;
  kind: BibleStudyNoteKind;
  ordinal: number;
  body: string;
  source: string;
  /** Which translation this commentary was written for. */
  version: string;
  version_name: string;
  /** Which study Bible it came from. */
  edition: string;
  edition_name: string;
}

export type BibleStudyNoteKind = 'commentary' | 'footnote' | 'introduction' | 'heading';

/**
 * One study Bible on top of an installed translation. A translation can have
 * several: the Nelson study Bible and the MacArthur study Bible are both NKJV,
 * and their commentary is not interchangeable.
 */
export interface BibleEditionInfo {
  code: string;
  version: string;
  name: string;
  publisher: string;
  language: string;
  license_class: 'public_domain' | 'licensed' | string;
  rights_holder: string;
  /** False when we know the study Bible but its notes are not on this server. */
  installed: boolean;
  note_count: number;
  note_kinds: BibleStudyNoteKind[];
  /** How to install it, or why it is absent. */
  note: string;
}

export interface BibleOtherTranslation {
  version: string;
  version_name: string;
  edition: string;
  edition_name: string;
  note_count: number;
}

export interface BibleEditionsResponse {
  version: string;
  default: string;
  editions: BibleEditionInfo[];
  /** Translations other than this one that carry study notes. */
  other_translations: BibleOtherTranslation[];
}

export interface BibleStudyNotesResponse {
  version: string;
  /** The study Bible that was used, resolved even when the client sent none. */
  edition: string;
  edition_name: string;
  requested: string | null;
  reference: string;
  kinds: BibleStudyNoteKind[];
  /** What this installed translation carries, even if none of it is for this passage. */
  available_kinds: Partial<Record<BibleStudyNoteKind, number>>;
  /** Whether notes from other translations were included. */
  cross_version: boolean;
  /** Translations other than this one that carry notes, so the toggle can say what it would add. */
  other_translations: BibleOtherTranslation[];
  /** Every installed study Bible for this translation, so the reader can switch. */
  editions: BibleEditionInfo[];
  notes: BibleStudyNote[];
  count: number;
  /** Why an empty list is empty, rather than leaving the panel blank. */
  note?: string | null;
}

/** One request fills the dashboard widget and the Android home screen. */
export interface BibleDailyResponse {
  day: string;
  verse_of_day: BibleVerseOfDay | { error: string };
  devotional: BibleDevotionalResponse;
  sources: BibleDevotionalSourceInfo[];
  streaks?: {
    read_streak_current: number;
    read_streak_longest: number;
    open_streak_current: number;
    days_read: number;
    chapters_read: number;
  };
  position?: BiblePosition | null;
}

// ── Reading aloud ───────────────────────────────────────────────────────────

export interface BibleVoicesResponse {
  voices: string[];
  count: number;
}

export interface BibleNarration {
  version: string;
  reference: string;
  /** The voice actually used, "default" when the engine picked one. */
  voice: string;
  verse_count: number;
  /** True when this passage was already narrated today or earlier. */
  cached: boolean;
  mime_type: string;
  length_bytes: number;
  /** Base64 WAV, played through a data: URL so no audio file is exposed. */
  audio_base64: string;
}

// ── Bible imports (Admin › Bible) ───────────────────────────────────────────

export type BibleImportKind = 'json' | 'pdf' | 'epub';

export interface BibleImportProviderInfo {
  code: string;
  title: string;
  base_url: string;
  /** The setting that has to hold a value before this provider can be used. */
  requires: string;
  configured: boolean;
  /** Why it cannot be used, naming the setting to fix. Empty when configured. */
  reason: string;
  note: string;
  translations?: BibleRemoteTranslation[];
}

export interface BibleRemoteTranslation {
  id: string;
  name: string;
  language: string;
  license_class: string;
  rights_holder: string;
  note: string;
}

export interface BibleImportRun {
  source: string;
  kind: string;
  code: string;
  name: string;
  provider: string;
  provider_id: string;
  status: 'succeeded' | 'failed' | string;
  message: string;
  verse_count: number | null;
  book_count: number | null;
  note_count: number | null;
  log: string[];
  duration_ms: number | null;
  created_at: string;
}

export interface BibleImportsResponse {
  /** The translation a reader gets with no choice made. */
  default_version: string;
  /** The translation the manifest marks primary, even if it is not installed yet. */
  primary: string;
  versions: BibleVersionInfo[];
  editions: BibleEditionInfo[];
  providers: BibleImportProviderInfo[];
  kinds: BibleImportKind[];
  /** Where uploads and fetched translations are staged; empty when unset. */
  import_dir: string;
  /** Why nothing can be installed yet, when the directory is not configured. */
  import_dir_error: string;
  runs: BibleImportRun[];
  /** The Calibre shelf inside Nextcloud, once an operator has pointed at one. */
  library_root: string;
  /** The setting that holds the shelf; the same name whatever the shelf is called. */
  library_setting: string;
  /** Why the shelf cannot be listed, naming the setting to fix. */
  library_error: string;
}

export interface BibleLibraryEntry {
  /** The full path on the shelf, which is what an import is given. */
  path: string;
  name: string;
  is_dir: boolean;
  size: number;
  /** json, pdf or epub; empty for a folder or a format we cannot read. */
  kind: BibleImportKind | '';
  /** False for a folder and for a file in a format we cannot read. */
  installable: boolean;
  /** Why this cannot be installed yet, in words the operator can read. */
  note: string;
}

export interface BibleLibraryListing {
  /** The folder that was listed, which is empty at the shelf root. */
  path: string;
  root: string;
  /** One level up, or empty at the root so the breadcrumb can hide itself. */
  parent: string;
  entries: BibleLibraryEntry[];
  count: number;
  installable: number;
}

/** What installing a translation online would cost, asked before spending anything. */
export interface BibleProviderEstimate {
  translation_id: string;
  name: string;
  books: number;
  /** Every chapter this translation has. */
  chapters: number;
  /** Of those, how many are already on disk and will not be requested again. */
  cached: number;
  /** Of those, how many would still be requested from the provider. */
  remaining: number;
  calls: number;
  cache_directory: string;
  /** The configured ceiling on new requests for one run; null when there is none. */
  budget: number | null;
  /** Whether this install fits inside the budget. */
  within_budget: boolean;
  /** False when no cache directory could be used, so every run refetches. */
  cache_enabled: boolean;
  /** Why caching is off, when it is. */
  cache_warning?: string;
}

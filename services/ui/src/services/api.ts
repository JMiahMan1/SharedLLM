import axios, { type AxiosRequestConfig } from 'axios';
import type { InternetOutage, NetworkStatus, NetworkUsage, RouterLogLine } from '../lib/network';
import { storageGetSync, storageSet } from '../lib/storage';
import { isUnreachable, recallPassage, rememberPassage, withOfflineCopy, type OfflinePassage } from '../lib/bibleOffline';
import type { FamilyMember } from '../components/chat/familyDirectory';
import { Capacitor } from '@capacitor/core';
import type { DeviceSortMode, WidgetVisibility, WidgetSize, DeviceEntry, CalendarPerson } from '../types/widget';
import type { ActiveCall } from '../types/api';
import type {
  SongJob,
  VoiceCheck,
  VoiceStatus,
  HealthStatus,
  ServiceInfo,
  LogEntry,
  Workspace,
  WorkspaceListResponse,
  UserProfileRaw,
  UserProfile,
  AccountAuditEvent,
  CredentialShares,
  ShareableService,
  APIKey,
  DiscoveredUser,
  DeviceAssignment,
  Role,
  RoleCatalog,
  EntityProtection,
  ShareRecipient,
  FamilyRecord,
  UsageDay,
  JarvisUse,
  ScreenRules,
  ScreenTimeStatus,
  ScreenTimeRequest,
  GuardRequest,
  SpotifyStatus,
  RecommendationLane,
  RecommendationLanes,
  FamilyRole,
  AiCapability,
  GlobalSetting,
  GatewayConfig,
  EsphomeDevice,
  CompanionDevice,
  BatteryReading,
  EtaResponse,
  DeviceActivity,
  StepSources,
  PairDeviceRequest,
  ExecutionResponse,
  ArcadeGamesResponse,
  ArcadeScoreboardResponse,
  ArcadeSort,
  ArcadeHighscoresResponse,
  TimerRecord,
  TimerDevicesResponse,
  SmokeTestResult,
  StorageEntry,
  RagStats,
  StorageStatus,
  Artifact,
  ArtifactListResponse,
  ArtifactFit,
  StorageUsage,
  SystemStorageUsage,
  RavenMission,
  WorkspaceAskMode,
  WorkspaceChat,
  WorkspaceChatEvent,
  WorkspaceChatSummary,
  RavenConfig,
  MediaGroup,
  LightCluster,
  LightPattern,
  TelemetryEnrollment,
  IntercomSessionData,
  IntercomConfigData,
  ImagePullResult,
  PullStatus,
  PullAndRestartResult,
  CheckUpdatesResponse,
  SearchResult,
  SystemHealthStatus,
  TelemetrySummary,
  TelemetryDataResponse,
  TelemetryInsights,
  ModelInfo,
  ModelsResponse,
  ModelSwitchResponse,
  GenerateRequest,
  GenerateResponse,
  EmbeddingsRequest,
  EmbeddingsResponse,
  TagsResponse,
  ShowRequest,
  WorkspaceFileReadResponse,
  WorkspaceFileListResponse,
  GitStatusResponse,
  GitBranchesResponse,
  GitCheckoutResponse,
  GitDiffResponse,
  GitCommitResponse,
  GitPushResponse,
  GitLogResponse,
  VolumesResponse,
  ServiceLogsResponse,
  ContainerExecResponse,
  RagIndexedPathsResponse,
  RagSyncFilesRequest,
  WorkspaceLintRequest,
  WorkspaceLintResponse,
  VolumesExecuteRequest,
  DiscoveryProfileResponse,
  NetworkScanRequest,
  NetworkScanResponse,
  Trip,
  TripUpdatePayload,
  TripsResponse,
  TripRouteResponse,
  TripLocationsResponse,
  LocationSuggestionsResponse,
  Workout,
  WorkoutsResponse,
  StepsResponse,
  StepRange,
  StepRangeResponse,
  MetricRangeResponse,
  MetricCatalogResponse,
  TimelineResponse,
  ActivityTrendsResponse,
  ActivityFeedResponse,
  ActivitySummaryResponse,
  ActivityWindow,
  AchievementsResponse,
  ActivityGoals,
  HealthProfile,
  HealthSummary,
  HealthWeekly,
  HealthChallenge,
  HealthChallengeKind,
  HealthChallengeWeek,
  SleepEstimate,
  GuardianRecap,
  GuardianConcern,
  MemberDevices,
  MdmFolder,
  MdmInventory,
  MdmSharedFile,
  MdmLibraryListing,
  MdmAuditEvent,
  GuardianPrompt,
  GuardianJournalNote,
  ActivityEvent,
  SleepSession,
  HealthCheckin,
  FoodEntry,
  FoodSearchItem,
  NutritionDay,
  HealthMetricHistory,
  HealthMetricSample,
  HealthShare,
  HealthFamilyPerson,
  HealthCycleState,
  HealthActiveMinutes,
  HealthFast,
  HealthStreaks,
  HealthGoalsWeek,
  HealthCheer,
  HealthCheersDay,
  FoodLabelDraft,
  RecipePhotoDraft,
  RecipeEstimate,
  HealthFastingState,
  HealthCycleSummary,
  HealthCyclePeriod,
  HealthCycleLog,
  StarsResponse,
  StarGrant,
  AdminStarGrantResponse,
  TelemetryNotification,
  TelemetryReport,
  TelemetryReportPeriod,
  TelemetryReportType,
  TelemetrySchedule,
  UserLiveLocation,
  WorkspaceSyncDirection,
  WorkspaceSyncResponse,
  WorkspaceUploadResponse,
  BibleVersionInfo,
  BibleBookInfo,
  BibleSearchResult,
  BibleVerseContext,
  BibleVerseOfDay,
  BibleDevotionalSourceInfo,
  BibleDevotionalResponse,
  BibleMark,
  BiblePosition,
  BiblePreferences,
  BibleStateResponse,
  BibleStreaks,
  BibleStats,
  BibleAchievementsResponse,
  BibleGame,
  BibleGameInfo,
  BibleGameMode,
  BibleGameResult,
  BibleGameReview,
  BibleLeaderboard,
  BibleMemoryOverview,
  BiblePlanDetail,
  BibleStudyDepth,
  BibleStudyStreamEvent,
  BiblePlanEnrollment,
  BiblePlanInfo,
  BibleMemoryVerse,
  BibleRecallResult,
  BibleActivitySummary,
  BibleActivityFeed,
  BibleEditionsResponse,
  BibleBlbLink,
  BibleInterlinear,
  BibleInterlinearCoverage,
  BibleInterlinearImport,
  BibleStudyNoteKind,
  BibleStudyNotesResponse,
  BibleDailyResponse,
  BibleVoicesResponse,
  BibleNarration,
  BibleNarrationChunk,
  BibleNarrationPlan,
  BibleImportKind,
  BibleImportRun,
  BibleImportsResponse,
  BibleImportProviderInfo,
  BibleRemoteTranslation,
  BibleProviderEstimate,
  BibleLibraryListing,
  BibleBookListing,
  BibleBookChapter,
  BibleImportedBook,
  BibleImportedBookDetail,
  BibleImportedBooksResponse,
  BibleDevotionalWork,
  BibleStudySession,
  BibleStudyMemoryResponse,
} from '../types/api';

// Re-export domain types so consumers can import them from the api module.
export type {
  SongJob,
  VoiceCheck,
  VoiceStatus,
  APIKey,
  GlobalSetting,
  HealthStatus,
  EsphomeDevice,
  CompanionDevice,
  BatteryReading,
  EtaResponse,
  DeviceActivity,
  DeviceEventRead,
  StepSources,
  PairDeviceRequest,
  DiscoveredDevice,
  LogEntry,
  RavenMission,
  RavenConfig,
  SearchResult,
  SmokeTestResult,
  UserProfile,
  Workspace,
  DeviceAssignment,
  Role,
  RoleCatalog,
  EntityProtection,
  ShareRecipient,
  FamilyRecord,
  FamilyRole,
  AiCapability,
  DiscoveredUser,
  RagStats,
  StorageStatus,
  TelemetryEnrollment,
  ExecutionResponse,
  ArcadeGamesResponse,
  ArcadeScoreboardResponse,
  TimerRecord,
  TimerTargetDevice,
  TimerDevicesResponse,
  StorageEntry,
  TalkConversation,
  TalkMessage,
  ImagePullResult,
  PullStatus,
  PullAndRestartResult,
  CheckUpdatesResponse,
  Trip,
  TripLocation,
  TripUpdatePayload,
  TripLocationSuggestion,
  LocationSuggestionsResponse,
  TripsResponse,
  TripRouteResponse,
  Workout,
  WorkoutsResponse,
  StepsResponse,
  StepRange,
  StepRangeResponse,
  ActivityTrendsResponse,
  ActivityFeedResponse,
  ActivitySummaryResponse,
  ActivityWindow,
  AchievementsResponse,
  ActivityGoals,
} from '../types/api';

declare module 'axios' {
  export interface InternalAxiosRequestConfig {
    __retryCount?: number;
    /**
     * Set for pre-auth / optional calls. A 401 on these must NOT clear the
     * stored session or redirect to /login; otherwise a call that races
     * authentication can log the user out (and loop).
     */
    skipAuthRedirect?: boolean;
    /**
     * Background calls (status polling, heartbeats): a failure is retried by
     * the caller's own schedule, so no "Reconnecting…" or "Cannot reach"
     * toast is shown for it (UX-45).
     */
    silent?: boolean;
  }
  export interface AxiosRequestConfig {
    skipAuthRedirect?: boolean;
    silent?: boolean;
  }
}

function getBaseUrl(): string {
  if (Capacitor.isNativePlatform()) {
    return 'http://localhost';
  }
  return window.location.origin;
}

const normalizeUser = (raw: UserProfileRaw): UserProfile => ({
  ...raw,
  full_name: raw.full_name ?? raw.display_name ?? '',
  role: raw.role ?? (raw.is_admin ? 'admin' : 'user'),
  is_admin: Boolean(raw.is_admin),
  voice_id: raw.voice_id ?? raw.voice_fingerprint ?? null,
});

/**
 * Base URL and auth headers for a request that bypasses `apiClient` -- one that
 * needs its own timeout, or a streamed body `fetch` can read but axios cannot.
 */
function directRequestConfig(): { baseURL: string; headers: Record<string, string> } {
  let baseURL = getBaseUrl();
  if (Capacitor.isNativePlatform()) {
    const serverUrl = storageGetSync('jarvis_server_url');
    if (serverUrl) baseURL = serverUrl;
  }
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = storageGetSync('jarvis_api_key');
  if (token) headers.Authorization = `Bearer ${token}`;
  return { baseURL, headers };
}

/**
 * POST a JSON body and hand each line of an NDJSON reply to ``onEvent`` as it
 * arrives. axios buffers whole responses, so streams go through fetch with the
 * same base URL and credentials axios would use.
 */
async function postNdjson<T>(
  path: string,
  body: unknown,
  onEvent: (event: T) => void,
  signal: AbortSignal | undefined,
  failure: string,
): Promise<void> {
  const { baseURL, headers } = directRequestConfig();
  const resp = await fetch(`${baseURL || ''}${path}`, { method: 'POST', headers, body: JSON.stringify(body), signal });
  if (!resp.ok || !resp.body) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail || `${failure} (${resp.status})`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf('\n');
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as T);
      newline = buffer.indexOf('\n');
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as T);
}

const mapUserPayload = (data: Partial<UserProfile>) => {
  const payload: Record<string, unknown> = { ...data };
  if ('full_name' in payload) {
    payload.display_name = payload.full_name;
    delete payload.full_name;
  }
  // The voice fingerprint is set by enrollment only; identity ignores it here.
  delete payload.voice_id;
  return payload;
};

/** The client defaults to JSON, which axios would apply to FormData too;
 *  multipart lets the browser add the boundary. Signing takes a few seconds. */
const VOICE_UPLOAD = { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120_000 };

/** A recording as multipart, named for its real container (Safari records MP4). */
function voiceForm(audio: Blob): FormData {
  const form = new FormData();
  form.append('file', audio, audio.type.includes('mp4') ? 'voice.m4a' : 'voice.webm');
  return form;
}

/** A list endpoint's records, whether it answers with the list itself or
 *  wraps it as `{ [key]: [...] }`. */
function listOf(data: unknown, key: string): Record<string, unknown>[] {
  const list = Array.isArray(data) ? data : (data as Record<string, unknown> | null)?.[key];
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
}

const normalizeWorkspaces = (data: WorkspaceListResponse): Workspace[] => {
  if (Array.isArray(data)) {
    return data;
  }
  if (Array.isArray(data?.workspaces)) {
    return data.workspaces;
  }
  return [];
};

/**
 * "all" is the UI's stand-in for "let the server work out who is asking", so it
 * must never be sent through as a literal username -- that would look up a user
 * named "all". This check was previously repeated at a dozen call sites, which
 * is a dozen chances to forget it on the next one.
 */
export function resolveUserId(userId?: string): string | undefined {
  if (!userId || userId === 'all') return undefined;
  return userId;
}

/** Adds `user_id` to a query/param bag only when we actually have a user. */
function withUserParam(target: URLSearchParams, userId?: string): void {
  const resolved = resolveUserId(userId);
  if (resolved) target.set('user_id', resolved);
}

export const apiClient = axios.create({
  baseURL: getBaseUrl(),
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 15000,
});

apiClient.interceptors.request.use((config) => {
  if (Capacitor.isNativePlatform()) {
    const serverUrl = storageGetSync('jarvis_server_url');
    if (serverUrl) {
      config.baseURL = serverUrl;
    }
    console.log('[API] baseURL:', config.baseURL, 'url:', config.url, 'method:', config.method);
  }
  const token = storageGetSync('jarvis_api_key');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  return config;
});

/**
 * Whether a failed request may be replayed.
 *
 * Only idempotent requests are safe to repeat. A proxy that gives up before a
 * slow endpoint answers (the public nginx cuts off at its default 60s) returns
 * 504, and retrying a POST there starts a second copy of the same side effect:
 * two drawing edits, two sent messages. GET/HEAD/OPTIONS can be repeated
 * without that. Kept separate from the interceptor so the rule is testable.
 */
export function isRetryableRequest(
  method: string | undefined,
  status: number | undefined,
  code: string | undefined,
): boolean {
  const verb = (method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(verb)) return false;
  return Boolean(
    (status && [408, 503, 504].includes(status)) ||
      (code && ['ECONNABORTED', 'ERR_NETWORK', 'ECONNREFUSED', 'ENOTFOUND'].includes(code)),
  );
}

export type ImageEditResult = {
  status: string;
  data?: Array<{ url?: string; b64_json?: string }>;
  message?: string;
  model?: string;
  job_id?: string;
};

function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } } | null)?.response?.status === 404;
}

function abortedError(): Error {
  const error = new Error('Image edit cancelled');
  error.name = 'AbortError';
  return error;
}

/**
 * Poll an image-edit job until it finishes.
 *
 * The edit runs on a CPU-offloaded model for minutes, so the request that
 * starts it returns at once and this loop asks for its state. The delay backs
 * off from 2s to 10s to keep a long edit from making hundreds of requests.
 * Extracted with injectable sleep/clock so the polling rules are testable.
 */
export async function waitForImageEdit(
  fetchJob: (jobId: string) => Promise<ImageEditResult>,
  jobId: string,
  options: {
    signal?: AbortSignal;
    timeoutMs?: number;
    initialDelayMs?: number;
    maxDelayMs?: number;
    sleep?: (ms: number) => Promise<void>;
    now?: () => number;
  } = {},
): Promise<ImageEditResult> {
  const timeoutMs = options.timeoutMs ?? 1_500_000;
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = options.now ?? (() => Date.now());
  const maxDelayMs = options.maxDelayMs ?? 10_000;
  const deadline = now() + timeoutMs;
  let delay = options.initialDelayMs ?? 2_000;

  while (now() < deadline) {
    if (options.signal?.aborted) throw abortedError();
    await sleep(delay);
    if (options.signal?.aborted) throw abortedError();
    let job: ImageEditResult;
    try {
      job = await fetchJob(jobId);
    } catch (error) {
      if (isNotFound(error)) {
        return {
          status: 'FAILURE',
          message: 'The image edit was interrupted by a server restart. Please try again.',
        };
      }
      throw error;
    }
    if (job.status === 'SUCCESS' || job.status === 'FAILURE' || job.status === 'ERROR') return job;
    delay = Math.min(Math.round(delay * 1.5), maxDelayMs);
  }
  return { status: 'FAILURE', message: 'The image edit did not finish in time. Please try again.' };
}

function newImageEditRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

async function fetchImageEditJob(jobId: string): Promise<ImageEditResult> {
  const resp = await apiClient.get(`/api/images/edit/jobs/${encodeURIComponent(jobId)}`, { timeout: 15_000 });
  return resp.data;
}

let isLoggingOut = false;
let lastConnectivityToast = 0;
// "Reconnected successfully!" needs the same throttle as the error toast. On a
// page with several polling queries, each request retries independently, so
// without this the same recovery is announced once per in-flight request.
let lastReconnectToast = 0;

/** How long a recovery announcement stays suppressed after the first one. */
export const RECONNECT_TOAST_WINDOW_MS = 15000;

/**
 * Whether a recovered connection is worth announcing.
 *
 * Extracted so the throttle can be tested directly. Driving it through axios's
 * interceptor machinery would mean mocking a callable default export, a
 * captured rejection handler and fake backoff timers -- a lot of harness to
 * assert one comparison.
 */
export function shouldAnnounceReconnect(
  now: number,
  lastAnnounced: number,
  windowMs: number = RECONNECT_TOAST_WINDOW_MS,
): boolean {
  return now - lastAnnounced > windowMs;
}
let lastFailedTarget = 'Jarvis server';

// Derive a human-friendly description of which service/endpoint failed so the
// reconnect toast can tell the user what is actually unreachable.
/** The service a request path belongs to, as people should read it. */
export function serviceForPath(path: string): string {
  if (path.startsWith('/api/media') || path.startsWith('/execute/media') || path.startsWith('/execute/audiobookshelf')) return 'Media service';
  if (path.startsWith('/api/execute') || path.startsWith('/execute/')) return 'Execution service';
  if (path.startsWith('/api/auth') || path.startsWith('/api/users')) return 'Identity service';
  if (path.includes('ma-jsonrpc') || path.includes('sendspin')) return 'Music Assistant';
  if (path.startsWith('/api/')) return 'Gateway';
  return 'Jarvis server';
}

function describeFailedTarget(config: AxiosRequestConfig | undefined, status?: number, code?: string): string {
  const method = (config?.method || 'GET').toString().toUpperCase();
  const path: string = config?.url?.toString() || '(unknown endpoint)';
  const service = serviceForPath(path);

  const detail = status ? ` (HTTP ${status})` : code ? ` (${code})` : '';
  const target = `${service}${detail} (${method} ${path})`;
  lastFailedTarget = target;
  return target;
}

/**
 * Re-throw an upstream failure carrying the server's own explanation.
 *
 * The response interceptor rejects the raw axios error, whose `message` is
 * only ever "Request failed with status code 400"; it drops the `detail` the
 * service actually sent. That is fine for connectivity noise but not for a
 * refusal an operator has to act on ("Unknown username(s) in the permit list:
 * alise"), so anything a human is expected to read goes through here rather
 * than being flattened into a status code.
 */
export function rethrowWithServerDetail(error: unknown, fallback: string): never {
  const data = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data;
  const detail = data?.detail;
  const message = typeof detail === 'string' && detail.trim()
    ? detail
    : (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string' ? detail[0].msg : null)
    ?? fallback;
  throw new Error(message);
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const isAxiosError = axios.isAxiosError(error);
    const config = error.config;

    // Check if error is retryable
    const status = error.response?.status;
    const code = error.code;
    const silent = config?.silent === true;
    const isRetryable = !silent && isAxiosError && isRetryableRequest(config?.method, status, code);

    if (config && isRetryable) {
      const currentRetry = config.__retryCount || 0;
      if (currentRetry < 3) {
        config.__retryCount = currentRetry + 1;

        const toastId = 'api-reconnecting';
        const { toast: t } = await import('react-hot-toast');
        const target = describeFailedTarget(config, status, code);
        t.loading(`Reconnecting to ${target} (attempt ${currentRetry + 1}/3)...`, {
          id: toastId,
          style: {
            background: 'rgba(59, 130, 246, 0.2)',
            color: '#93c5fd',
            border: '1px solid rgba(59, 130, 246, 0.3)',
            fontSize: '12px',
          },
        });

        // 1s, 2s, 4s backoff + 0-1s random jitter
        const delay = Math.pow(2, currentRetry) * 1000 + Math.random() * 1000;
        await new Promise((resolve) => setTimeout(resolve, delay));

        try {
          const res = await apiClient(config);
          t.dismiss(toastId);
          // Announce recovery once per outage, not once per request. A page
          // with several polling queries has many in flight, and they all
          // succeed within the same second.
          const now = Date.now();
          if (shouldAnnounceReconnect(now, lastReconnectToast)) {
            lastReconnectToast = now;
            t.success('Reconnected successfully!', {
              id: toastId,
              duration: 2000,
              style: {
                background: 'rgba(16, 185, 129, 0.2)',
                color: '#a7f3d0',
                border: '1px solid rgba(16, 185, 129, 0.3)',
                fontSize: '12px',
              },
            });
          }
          return res;
        } catch (retryErr) {
          // Pass the error down to the next retry or final error handler
          return Promise.reject(retryErr);
        }
      } else {
        // Exceeded retries, dismiss the loading toast
        const { toast: t } = await import('react-hot-toast');
        t.dismiss('api-reconnecting');
      }
    }

    // Connectivity error toast check (throttled, now on ALL platforms)
    const isConnectivityError = isAxiosError && (
      code === 'ECONNABORTED' ||
      code === 'ENOTFOUND' ||
      code === 'ECONNREFUSED' ||
      code === 'ERR_NETWORK'
    );

    if (isConnectivityError && !silent) {
      const now = Date.now();
      if (now - lastConnectivityToast > 15000) {
        lastConnectivityToast = now;
        const { toast: t } = await import('react-hot-toast');
        t.error(`Cannot reach ${lastFailedTarget}. Check your network or that the service is running.`, {
          duration: 8000,
          style: {
            background: 'rgba(239, 68, 68, 0.2)',
            color: '#fca5a5',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            fontSize: '12px',
          },
        });
      }
    }

    console.error('[API] Response error:', error.message, error.config?.baseURL, error.config?.url);
    if (error.response?.status === 401 && !isLoggingOut) {
      const isLoginRequest = error.config?.url?.includes('/api/auth/login');
      // Pre-auth/optional calls (e.g. the site theme preference, which loads
      // before AuthProvider resolves) are allowed to 401 without tearing down
      // the session; otherwise the app bounces to /login on every start.
      const isOptional = error.config?.skipAuthRedirect === true;
      if (!isLoginRequest && !isOptional) {
        isLoggingOut = true;
        const { storageRemove } = await import('../lib/storage');
        await storageRemove('jarvis_api_key');
        await storageRemove('jarvis_user');
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  },
);

/** Most trips a single request returns (geo caps it too). */
export const TRIPS_FETCH_LIMIT = 500;

export const api = {
  async login(username: string, password: string): Promise<{ api_key: string; username: string; is_admin: boolean }> {
    const resp = await apiClient.post('/api/auth/login', { username, password });
    return resp.data;
  },

  async getMe(): Promise<UserProfile> {
    const resp = await apiClient.get('/api/users/me');
    return normalizeUser(resp.data);
  },

  async discoverUsers(): Promise<{ users: DiscoveredUser[]; warnings: string[]; errors: string[] }> {
    const resp = await apiClient.get('/api/auth/discover');
    return resp.data;
  },

  async setUpServiceToken(
    username: string,
    service: 'home_assistant' | 'nextcloud' | 'audiobookshelf',
    password?: string,
    source_username?: string,
  ): Promise<{ success: boolean; username: string; service: string; message: string; detail?: string }> {
    const resp = await apiClient.post(`/api/users/${username}/service-token`, {
      service,
      password,
      source_username,
    });
    return resp.data;
  },

  async getUsers(): Promise<UserProfile[]> {
    const resp = await apiClient.get('/api/users');
    return (resp.data ?? []).map(normalizeUser);
  },

  async createUser(data: Partial<UserProfile> & { username: string }): Promise<UserProfile> {
    const resp = await apiClient.post('/api/users', mapUserPayload(data));
    return normalizeUser(resp.data);
  },

  async updateUser(username: string, data: Partial<UserProfile>): Promise<UserProfile> {
    const resp = await apiClient.patch(`/api/users/${username}`, mapUserPayload(data));
    return normalizeUser(resp.data);
  },

  async deleteUser(username: string): Promise<{ status?: string; success?: boolean }> {
    const resp = await apiClient.delete(`/api/users/${username}`);
    return resp.data;
  },

  /** The household's service URLs (system config first), to prefill login forms. No secrets. */
  async getServiceDefaults(): Promise<{ urls: Record<string, string | null>; source: Record<string, string> }> {
    const resp = await apiClient.get('/api/users/service-defaults');
    const { source = {}, ...urls } = (resp.data ?? {}) as Record<string, unknown> & { source?: Record<string, string> };
    return { urls: urls as Record<string, string | null>, source };
  },

  /** A saved credential's value, for a password field's eye. Owner or admin; audited. */
  async revealCredential(username: string, field: string): Promise<string> {
    const resp = await apiClient.post(`/api/users/${encodeURIComponent(username)}/reveal`, { field });
    return String(resp.data?.value ?? '');
  },

  /** Account audit trail (admin): who changed which account fields, never values. */
  async getAccountAudit(params: { target?: string; actor?: string; action?: string; limit?: number } = {}): Promise<AccountAuditEvent[]> {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== '' && query.set(k, String(v)));
    const resp = await apiClient.get(`/api/admin/audit?${query.toString()}`);
    return resp.data?.events ?? [];
  },

  async getMyAccountAudit(): Promise<AccountAuditEvent[]> {
    const resp = await apiClient.get('/api/users/me/audit');
    return resp.data?.events ?? [];
  },

  async chooseAvatar(preset: string): Promise<UserProfile> {
    const resp = await apiClient.put('/api/users/me/avatar', { preset });
    return normalizeUser(resp.data);
  },

  async uploadAvatar(dataUrl: string): Promise<UserProfile> {
    const resp = await apiClient.post('/api/users/me/avatar/image', { data_url: dataUrl });
    return normalizeUser(resp.data);
  },

  async removeAvatar(): Promise<UserProfile> {
    const resp = await apiClient.delete('/api/users/me/avatar');
    return normalizeUser(resp.data);
  },

  /** A family member's uploaded picture, fetched with the user's credentials. */
  async getAvatarImage(username: string): Promise<Blob> {
    const resp = await apiClient.get(`/api/users/${encodeURIComponent(username)}/avatar/image`, { responseType: 'blob' });
    return resp.data;
  },

  async updateProfile(data: Partial<UserProfile>): Promise<UserProfile> {
    const resp = await apiClient.patch('/api/users/me', mapUserPayload(data));
    return normalizeUser(resp.data);
  },

  /** Which shared (system default) services this user may borrow. */
  async getCredentialShares(username: string): Promise<CredentialShares> {
    const resp = await apiClient.get(`/api/users/${username}/credential-shares`);
    return resp.data;
  },

  /** Grant/revoke shared services. Identity allows admins only. */
  async putCredentialShares(
    username: string,
    services: ShareableService[],
    note?: string,
  ): Promise<CredentialShares> {
    const resp = await apiClient.put(`/api/users/${username}/credential-shares`, {
      services,
      note: note ?? null,
    });
    return resp.data;
  },
  // Admin > Network: the router monitor (docs/NETWORK.md).
  async getNetworkStatus(): Promise<NetworkStatus> {
    return (await apiClient.get('/api/network/status')).data;
  },
  async checkNetwork(): Promise<NetworkStatus> {
    return (await apiClient.post('/api/network/check', {}, { timeout: 60_000 })).data;
  },
  async saveRouterSettings(body: { url: string; username: string; password?: string }): Promise<NetworkStatus> {
    return (await apiClient.put('/api/network/settings', body, { timeout: 60_000 })).data;
  },
  async getRouterLog(params: { limit?: number; level?: string; q?: string } = {}): Promise<{ lines: RouterLogLine[] }> {
    return (await apiClient.get('/api/network/log', { params })).data;
  },
  async getNetworkUsage(hours: number): Promise<NetworkUsage> {
    return (await apiClient.get('/api/network/usage', { params: { hours } })).data;
  },
  async getInternetOutages(days = 30): Promise<{ days: number; outages: InternetOutage[] }> {
    return (await apiClient.get('/api/network/outages', { params: { days } })).data;
  },
  async getUserTheme(): Promise<{ theme_id: string; packs: unknown[] }> {
    // Runs before/independently of auth, so a 401 must not log the user out.
    const resp = await apiClient.get('/api/users/me/theme', { skipAuthRedirect: true });
    return resp.data;
  },

  async updateUserTheme(body: {
    theme_id?: string;
    packs?: unknown[];
  }): Promise<{ status: string; theme_id: string; packs: unknown[] }> {
    const resp = await apiClient.put('/api/users/me/theme', body);
    return resp.data;
  },

  async getActivitySharing(): Promise<{
    enabled: boolean;
    audience: 'circle' | 'users';
    user_ids: string[];
    share: string[];
    /** child/teen: circle sharing is mandatory and the toggle is disabled. */
    sharing_locked?: boolean;
  }> {
    const resp = await apiClient.get('/api/users/me/activity-sharing');
    return resp.data;
  },

  /**
   * Accounts an activity-sharing grant may name.
   *
   * Deliberately NOT `getUsers()`: that route is admin-only, so using it here
   * left every non-admin with an empty picker and "Everyone" as their only
   * choice. This returns username + display name and nothing else.
   */
  /** Everyone in the household with their chat picture and Talk account. */
  async getFamilyDirectory(): Promise<FamilyMember[]> {
    const resp = await apiClient.get('/api/users/family-directory');
    return resp.data;
  },

  async getSharingRecipients(): Promise<ShareRecipient[]> {
    const resp = await apiClient.get('/api/users/sharing-recipients');
    return resp.data ?? [];
  },

  /** One person's recent days of phone usage (newest first); parents' or their own. */
  async getUsage(username: string, days = 7): Promise<{ status: string; days: UsageDay[] }> {
    const resp = await apiClient.get(`/api/usage/${encodeURIComponent(username)}`, { params: { days } });
    return resp.data;
  },

  /** What the caller does most inside Jarvis (Phase 10), from their own feed. */
  async getJarvisUse(days = 7): Promise<{ status: string; use: JarvisUse }> {
    const resp = await apiClient.get('/api/activity/use', { params: { days } });
    return resp.data;
  },

  /** Time left today and whether the phone should be locked (the member or their parents). */
  async getScreenTimeStatus(username: string): Promise<ScreenTimeStatus> {
    const resp = await apiClient.get(`/api/screen-time/${encodeURIComponent(username)}/status`);
    return resp.data;
  },

  /** The phone reports Jarvis's protection being switched off; the parents hear. */
  async reportGuardTamper(event: 'admin_disabled' | 'guard_disabled' | 'guard_missing' | 'uninstall_attempt'): Promise<void> {
    await apiClient.post('/api/guard/tamper', { event });
  },

  async setScreenRules(username: string, rules: ScreenRules): Promise<{ rules: ScreenRules }> {
    const resp = await apiClient.put(`/api/screen-time/${encodeURIComponent(username)}/rules`, rules);
    return resp.data;
  },

  /** `{}` pauses until lifted, `{ until }` for a while, `{ resume: true }` lifts it. */
  async pauseScreenTime(username: string, body: { until?: string; resume?: boolean }): Promise<{ rules: ScreenRules }> {
    const resp = await apiClient.post(`/api/screen-time/${encodeURIComponent(username)}/pause`, body);
    return resp.data;
  },

  async giveScreenTimeBonus(username: string, minutes: number): Promise<{ request: ScreenTimeRequest }> {
    const resp = await apiClient.post(`/api/screen-time/${encodeURIComponent(username)}/bonus`, { minutes });
    return resp.data;
  },

  async getScreenTimeRequests(status: 'pending' | 'granted' | 'declined' = 'pending'): Promise<{ requests: ScreenTimeRequest[] }> {
    const resp = await apiClient.get('/api/screen-time/requests', { params: { status } });
    return resp.data;
  },

  async askForScreenTime(minutes: number, note?: string): Promise<{ request: ScreenTimeRequest }> {
    const resp = await apiClient.post('/api/screen-time/requests', { minutes, note });
    return resp.data;
  },

  async decideScreenTimeRequest(id: number, grant: boolean, minutes?: number): Promise<{ request: ScreenTimeRequest }> {
    const resp = await apiClient.post(`/api/screen-time/requests/${id}`, { grant, ...(minutes ? { minutes } : {}) });
    return resp.data;
  },

  /** Whether Spotify linking is set up here, and the caller's own link. */
  async getSpotifyStatus(): Promise<SpotifyStatus> {
    const resp = await apiClient.get('/api/spotify/status');
    return resp.data;
  },

  /** Where to send the browser to link the caller's Spotify. */
  async startSpotifyLink(): Promise<{ url: string }> {
    const resp = await apiClient.post('/api/spotify/link');
    return resp.data;
  },

  async unlinkSpotify(): Promise<{ linked: boolean }> {
    const resp = await apiClient.delete('/api/spotify/link');
    return resp.data;
  },

  /** Asking a parent first: the caller's own requests and their children's. */
  async getGuardRequests(status: 'pending' | 'approved' | 'denied' = 'pending'): Promise<{ requests: GuardRequest[] }> {
    const resp = await apiClient.get('/api/guard/requests', { params: { status } });
    return resp.data;
  },

  /** A child asks a parent first (the phone asks for new apps and permissions itself). */
  async askAParent(kind: GuardRequest['kind'], target: string, note?: string): Promise<{ request: GuardRequest }> {
    const resp = await apiClient.post('/api/guard/requests', { kind, target, ...(note ? { note } : {}) });
    return resp.data;
  },

  /** A parent answers; a site is allowed for `minutes` or `always`. */
  async decideGuardRequest(id: number, body: { approve: boolean; minutes?: number; always?: boolean }): Promise<{ request: GuardRequest }> {
    const resp = await apiClient.post(`/api/guard/requests/${id}`, body);
    return resp.data;
  },

  /** Which suggestion lanes are on for a member (the member or their parents). */
  async getRecommendationLanes(username: string): Promise<RecommendationLanes> {
    const resp = await apiClient.get(`/api/recommendation-lanes/${encodeURIComponent(username)}`);
    return resp.data;
  },

  /** A parent blesses (true) or blocks (false) lanes; lanes left out keep their setting. */
  async setRecommendationLanes(username: string, lanes: Partial<Record<RecommendationLane, boolean>>): Promise<RecommendationLanes> {
    const resp = await apiClient.put(`/api/recommendation-lanes/${encodeURIComponent(username)}`, { lanes });
    return resp.data;
  },

  /** Whether the caller has an offline unlock code for their children's phones. */
  async getOfflineCode(): Promise<{ set: boolean; updated_at: string | null; can_set: boolean }> {
    const resp = await apiClient.get('/api/guard/offline-code');
    return resp.data;
  },

  async setOfflineCode(code: string): Promise<{ set: boolean; updated_at: string }> {
    const resp = await apiClient.put('/api/guard/offline-code', { code });
    return resp.data;
  },

  async clearOfflineCode(): Promise<{ set: boolean }> {
    const resp = await apiClient.delete('/api/guard/offline-code');
    return resp.data;
  },

  /** A child's devices with their technical side: the father's and admins' only (403 for anyone else). */
  async getMemberDevices(username: string): Promise<MemberDevices> {
    const resp = await apiClient.get(`/api/family/member/${encodeURIComponent(username)}/devices`);
    return resp.data;
  },

  // --- Device management (MDM, HANDOVER.md Phase 12) -----------------------

  /** Every family device in one list: the father's and admins' only. */
  async getMdmDevices(): Promise<MdmInventory> {
    const resp = await apiClient.get('/api/mdm/devices');
    return resp.data;
  },

  /** Push an app-update notice to the owners of the selected devices (admin). */
  async pushMdmAppUpdate(body: { device_keys?: string[]; usernames?: string[] }): Promise<{ sent: number; owners: string[]; version: string }> {
    const resp = await apiClient.post('/api/mdm/devices/update', body);
    return resp.data;
  },

  /** Queue a remote wipe on a device-owner phone; the phone erases itself on its next check-in. */
  async wipeMdmDevice(deviceKey: string): Promise<{ status: string; delivery: string }> {
    const resp = await apiClient.post(`/api/mdm/devices/${encodeURIComponent(deviceKey)}/wipe`, { confirm: true });
    return resp.data;
  },

  /** The shared School and Library folders, their files and who each is pushed to (admin). */
  async getMdmFiles(): Promise<{ folders: MdmFolder[] }> {
    const resp = await apiClient.get('/api/mdm/files');
    return resp.data;
  },

  /** Upload one file into a shared folder (admin). */
  async uploadMdmFile(folder: string, file: File): Promise<{ file: MdmSharedFile }> {
    const form = new FormData();
    form.append('file', file);
    const resp = await apiClient.post(`/api/mdm/files/${encodeURIComponent(folder)}`, form);
    return resp.data;
  },

  /** Remove one file from a shared folder (admin). */
  async deleteMdmFile(folder: string, name: string): Promise<{ status: string }> {
    const resp = await apiClient.delete(`/api/mdm/files/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`);
    return resp.data;
  },

  /** Push a shared folder to selected people's devices (admin). */
  async pushMdmFiles(folder: string, usernames: string[]): Promise<{ pushed_to: string[] }> {
    const resp = await apiClient.post('/api/mdm/files/push', { folder, usernames });
    return resp.data;
  },

  /** The shared folders pushed to the caller, for the device-facing list. */
  async getMdmFilesForMe(): Promise<{ folders: Array<{ folder: string; files: MdmSharedFile[] }> }> {
    const resp = await apiClient.get('/api/mdm/files/for-me');
    return resp.data;
  },

  /** One shared file's bytes, so the app can preview or download it with the key. */
  async getMdmFileBlob(folder: string, name: string): Promise<Blob> {
    const resp = await apiClient.get(
      `/api/mdm/files/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`,
      { responseType: 'blob' },
    );
    return resp.data;
  },

  /** The read-only family Library set, sourced from the Calibre shelf. */
  async getMdmLibraryBooks(): Promise<MdmLibraryListing> {
    const resp = await apiClient.get('/api/mdm/library/books');
    return resp.data;
  },

  /** One Library book's bytes (read-only). */
  async getMdmLibraryBookBlob(path: string): Promise<Blob> {
    const resp = await apiClient.post('/api/mdm/library/books/file', { path }, { responseType: 'blob' });
    return resp.data;
  },

  /** Who did what to the family's devices and shared files, newest first (admin). */
  async getMdmAudit(limit = 100): Promise<{ events: MdmAuditEvent[] }> {
    const resp = await apiClient.get('/api/mdm/audit', { params: { limit } });
    return resp.data;
  },

  /** What content monitoring found on a child's phone, newest first (parents only). */
  async getGuardianConcerns(username: string, limit = 50): Promise<{ concerns: GuardianConcern[] }> {
    const resp = await apiClient.get(`/api/guardian/concerns/${encodeURIComponent(username)}`, { params: { limit } });
    return resp.data;
  },

  /** A child's week (parents only). */
  async getGuardianRecap(username: string, day?: string): Promise<{ recap: GuardianRecap }> {
    const resp = await apiClient.get(`/api/guardian/recap/${encodeURIComponent(username)}`, { params: day ? { day } : {} });
    return resp.data;
  },

  async getGuardianGuidance(username: string): Promise<{ week: { start: string; end: string }; prompts: GuardianPrompt[] }> {
    const resp = await apiClient.get(`/api/guardian/guidance/${encodeURIComponent(username)}`);
    return resp.data;
  },

  async getGuardianActivity(username: string, limit = 100): Promise<{ events: ActivityEvent[] }> {
    const resp = await apiClient.get(`/api/guardian/activity/${encodeURIComponent(username)}`, { params: { limit } });
    return resp.data;
  },

  /** Ring another member's phone (a parent's Find my phone for a child). */
  async ringMemberPhone(username: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/guardian/find-phone/${encodeURIComponent(username)}`);
    return resp.data;
  },

  async getGuardianJournal(username: string): Promise<{ notes: GuardianJournalNote[] }> {
    const resp = await apiClient.get(`/api/guardian/journal/${encodeURIComponent(username)}`);
    return resp.data;
  },

  async addGuardianJournal(username: string, text: string, shared: boolean): Promise<GuardianJournalNote> {
    const resp = await apiClient.post(`/api/guardian/journal/${encodeURIComponent(username)}`, { text, shared });
    return resp.data;
  },

  async deleteGuardianJournal(username: string, id: number): Promise<void> {
    await apiClient.delete(`/api/guardian/journal/${encodeURIComponent(username)}/${id}`);
  },

  /** A child's own parents, for "I need to talk". */
  async getMyParents(): Promise<{ parents: string[] }> {
    const resp = await apiClient.get('/api/guardian/my-parents');
    return resp.data;
  },

  async askToTalk(to: string[], note: string): Promise<{ told: string[] }> {
    const resp = await apiClient.post('/api/guardian/talk', { to, note });
    return resp.data;
  },

  /** Household roster with family roles; parent/household-manager only. */
  async getFamily(): Promise<FamilyRecord[]> {
    const resp = await apiClient.get('/api/users/family');
    return resp.data ?? [];
  },

  async updateFamilyMember(
    username: string,
    body: { family_role?: FamilyRole; mother_username?: string | null; clear_mother?: boolean },
  ): Promise<FamilyRecord> {
    const resp = await apiClient.patch(`/api/users/${username}/family`, body);
    return resp.data;
  },

  async updateActivitySharing(body: {
    enabled?: boolean;
    audience?: 'circle' | 'users';
    user_ids?: string[];
    share?: string[];
  }): Promise<{ status: string; enabled: boolean; audience: string; user_ids: string[]; share: string[] }> {
    const resp = await apiClient.put('/api/users/me/activity-sharing', body);
    return resp.data;
  },

  /** How far the signed-in user's voice enrollment has got. */
  async getMyVoice(): Promise<VoiceStatus> {
    const resp = await apiClient.get('/api/users/me/voice');
    return resp.data;
  },

  /** Add one recording to the user's voice profile (signed on the server). */
  async enrollVoice(audio: Blob): Promise<VoiceStatus & { speech_seconds: number }> {
    const resp = await apiClient.post('/api/users/me/enroll', voiceForm(audio), VOICE_UPLOAD);
    return resp.data;
  },

  /** Who the household's voice matching thinks is speaking in `audio`. */
  async checkMyVoice(audio: Blob): Promise<VoiceCheck> {
    const resp = await apiClient.post('/api/users/me/voice/check', voiceForm(audio), VOICE_UPLOAD);
    return resp.data;
  },

  async forgetMyVoice(): Promise<VoiceStatus> {
    const resp = await apiClient.delete('/api/users/me/voice');
    return resp.data;
  },

  async getHealth(): Promise<HealthStatus> {
    const resp = await apiClient.get('/health/ready');
    return resp.data;
  },

  /** Documents the Docs page can open (docs/*.md and the README). */
  async listDocs(): Promise<Array<{ name: string; title: string }>> {
    const resp = await apiClient.get('/api/docs');
    return Array.isArray(resp.data?.docs) ? resp.data.docs : [];
  },

  /** One document's markdown. Goes through apiClient so the phone app reaches
   * the server (a bare relative fetch hit the app's own localhost). */
  async getDoc(name: string): Promise<string> {
    const resp = await apiClient.get(`/api/docs/${encodeURIComponent(name)}`);
    return String(resp.data?.content ?? '');
  },

  async getInfo(): Promise<ServiceInfo | null> {
    try {
      const resp = await apiClient.get('/info');
      return resp.data;
    } catch {
      return null;
    }
  },

  async chat(message: string, workspaceId?: string, userId?: string, stream = false): Promise<unknown> {
    // A non-streaming turn is billed to the model, so it outlives the 15s
    // axios default: the quick assistant aborted every question it asked and
    // showed a failure for a request the server answered seconds later.
    const resp = await apiClient.post(
      '/api/chat',
      {
        query: message,
        workspace_id: workspaceId,
        user_id: userId,
        stream,
      },
      { timeout: 640_000 },
    );
    return resp.data;
  },

  async getGatewayConfig(): Promise<GatewayConfig> {
    const resp = await apiClient.get('/api/config');
    return resp.data.config;
  },

  async updateGatewayConfig(config: Partial<GatewayConfig>): Promise<GatewayConfig> {
    const resp = await apiClient.post('/api/config', config);
    return resp.data.config;
  },

  async getAvailableModels(): Promise<string[]> {
    const resp = await apiClient.get('/api/config/models');
    if (resp.data.status === 'ERROR' || !resp.data.models) {
      console.warn('Failed to fetch available models:', resp.data.message || 'No models returned');
      return [];
    }
    return resp.data.models;
  },

  async globalSearch(query: string, signal?: AbortSignal): Promise<SearchResult> {
    const url = `/api/search?q=${encodeURIComponent(query)}`;
    // Only pass a config object when there is something to cancel with, so
    // callers that do not abort see the plain request shape.
    const resp = signal ? await apiClient.get(url, { signal }) : await apiClient.get(url);
    return resp.data as SearchResult;
  },

  async getLogs(limit = 50, levels?: string[]): Promise<LogEntry[]> {
    // Admin only: these are the whole system's logs. `levels` (e.g.
    // ['warning', 'error']) filters server-side over the whole buffer.
    const level = levels?.length ? `&level=${encodeURIComponent(levels.join(','))}` : '';
    const resp = await apiClient.get(`/api/admin/logs?limit=${limit}${level}`);
    // Always a list: the header and dashboard call .filter on it, and an
    // error body ({detail: ...}) used to take the whole app down.
    return Array.isArray(resp.data) ? resp.data : [];
  },

  getLogWebSocketUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const token = storageGetSync('jarvis_api_key') || '';
    return `${protocol}//${host}/api/logs/stream?token=${encodeURIComponent(token)}`;
  },

  async getSettings(): Promise<GlobalSetting[]> {
    const resp = await apiClient.get('/api/settings');
    return resp.data;
  },

  async updateSetting(key: string, value: string): Promise<GlobalSetting> {
    const resp = await apiClient.patch(`/api/settings/${key}`, { value });
    return resp.data;
  },

  async updateSettingsBulk(settings: Record<string, string>): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/settings', settings);
    return resp.data;
  },

  async getDnsRecords(): Promise<Array<{
    id: number;
    domain: string;
    record_type: string;
    values: string[];
    ttl: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
  }>> {
    const resp = await apiClient.get('/api/dns');
    return resp.data;
  },

  async createDnsRecord(record: {
    domain: string;
    record_type: string;
    values: string[];
    ttl: number;
  }): Promise<{
    id: number;
    domain: string;
    record_type: string;
    values: string[];
    ttl: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
  }> {
    const resp = await apiClient.post('/api/dns', record);
    return resp.data;
  },

  async updateDnsRecord(id: number, record: {
    domain?: string;
    record_type?: string;
    values?: string[];
    ttl?: number;
    is_active?: boolean;
  }): Promise<{
    id: number;
    domain: string;
    record_type: string;
    values: string[];
    ttl: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
  }> {
    const resp = await apiClient.put(`/api/dns/${id}`, record);
    return resp.data;
  },

  async deleteDnsRecord(id: number): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/dns/${id}`);
    return resp.data;
  },

  // Presence
  async getUserPresence(userId: string): Promise<{ status: string; user_id: string; presence: { room: string; confidence: number } | null }> {
    const resp = await apiClient.get(`/api/presence/${userId}`);
    return resp.data;
  },

  async getAllPresence(): Promise<{ status: string; presence: Record<string, { room: string; confidence: number }> }> {
    const resp = await apiClient.get('/api/presence/all');
    return resp.data;
  },

  async getPresenceRooms(): Promise<{ status: string; rooms: string[] }> {
    const resp = await apiClient.get('/api/presence/rooms');
    return resp.data;
  },

  // Location
  async updateUserLocation(userId: string, location: {
    latitude: number;
    longitude: number;
    accuracy?: number;
    speed?: number;
    bearing?: number;
    timestamp?: number;
  }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/users/${userId}/location`, location);
    return resp.data;
  },

  async getUserLocation(userId: string): Promise<{
    latitude: number;
    longitude: number;
    accuracy?: number;
    speed?: number;
    bearing?: number;
    timestamp?: number;
  }> {
    const resp = await apiClient.get(`/api/users/${userId}/location`);
    return resp.data;
  },

  // Life360 & Vehicle Telemetry
  async getVehicles(): Promise<{ vehicles: Array<{ id: string; name: string; mpg: number; cost_per_gallon: number; fuel_type: string; fuel_price_location?: string; fuel_price_source?: string; fuel_price_updated_at?: string }> }> {
    const resp = await apiClient.get('/api/geo/vehicles');
    return resp.data;
  },

  async saveVehicle(vehicle: { id: string; name: string; mpg: number; cost_per_gallon: number; fuel_type?: string; fuel_price_location?: string }): Promise<{ status: string; vehicle: Record<string, unknown> }> {
    const resp = await apiClient.post('/api/geo/vehicles', vehicle);
    return resp.data;
  },

  async deleteVehicle(vehicleId: string): Promise<{ status: string; deleted: string }> {
    const resp = await apiClient.delete(`/api/geo/vehicles/${encodeURIComponent(vehicleId)}`);
    return resp.data;
  },

  async getAssignedVehicle(userId: string): Promise<{ user_id: string; vehicle_id: string | null; vehicle: Record<string, unknown> | null }> {
    const resp = await apiClient.get(`/api/geo/vehicles/assigned/${encodeURIComponent(userId)}`);
    return resp.data;
  },

  async assignVehicle(userId: string, vehicleId: string | null): Promise<{ status: string; user_id: string; vehicle_id: string | null }> {
    const resp = await apiClient.post('/api/geo/vehicles/assign', { user_id: userId, vehicle_id: vehicleId });
    return resp.data;
  },

  // Fuel Price & Vehicle Lookup
  async getFuelPrices(location: string): Promise<{
    location: string;
    source: string;
    prices: { regular: number | null; midgrade: number | null; premium: number | null; diesel: number | null };
  }> {
    const resp = await apiClient.get(`/api/geo/fuel-prices?location=${encodeURIComponent(location)}`);
    return resp.data;
  },

  async getVehicleLookupYears(): Promise<{ menuItem: Array<{ text: string; value: string }> }> {
    const resp = await apiClient.get('/api/geo/vehicle-lookup/years');
    return resp.data;
  },

  async getVehicleLookupMakes(year: number): Promise<{ menuItem: Array<{ text: string; value: string }> }> {
    const resp = await apiClient.get(`/api/geo/vehicle-lookup/makes?year=${year}`);
    return resp.data;
  },

  async getVehicleLookupModels(year: number, make: string): Promise<{ menuItem: Array<{ text: string; value: string }> }> {
    const resp = await apiClient.get(`/api/geo/vehicle-lookup/models?year=${year}&make=${encodeURIComponent(make)}`);
    return resp.data;
  },

  async getVehicleLookupOptions(year: number, make: string, model: string): Promise<{ menuItem: Array<{ text: string; value: string }> | { text: string; value: string } }> {
    const resp = await apiClient.get(`/api/geo/vehicle-lookup/options?year=${year}&make=${encodeURIComponent(make)}&model=${encodeURIComponent(model)}`);
    return resp.data;
  },

  async getVehicleLookupDetail(vehicleId: string): Promise<{
    year: number; make: string; model: string;
    comb08: number; city08: number; highway08: number;
    fuelType1: string;
  }> {
    const resp = await apiClient.get(`/api/geo/vehicle-lookup/${encodeURIComponent(vehicleId)}`);
    return resp.data;
  },

  async getVehicleLookupVin(vin: string): Promise<{
    vin: string;
    year: number;
    make: string;
    model: string;
    trim?: string;
    comb08?: number | null;
    city08?: number | null;
    highway08?: number | null;
    fuelType1: string;
    displ?: string;
    cylinders?: string;
    drive?: string;
  }> {
    const resp = await apiClient.get(`/api/geo/vehicle-lookup/vin/${encodeURIComponent(vin.trim().toUpperCase())}`);
    return resp.data;
  },

  async getGeoTelemetry(userId: string, hours = 24): Promise<{
    status: string;
    entity_id: string;
    friendly_name: string;
    latitude: number;
    longitude: number;
    accuracy?: number;
    battery?: number;
    is_moving: boolean;
    current_speed_mph: number;
    top_speed_mph: number;
    current_zone?: string | null;
    closest_zone?: string | null;
    closest_zone_distance_miles?: number | null;
    distance_to_home_miles?: number | null;
    dwell_time_seconds: number;
    dwell_time_formatted: string;
    distance_traveled_miles: number;
    frequented_locations: Array<{ name: string; dwell_seconds: number; dwell_formatted: string }>;
    vehicle?: {
      id: string;
      name: string;
      mpg: number;
      cost_per_gallon: number;
      fuel_type: string;
      gallons_used?: number;
      estimated_cost_usd?: number;
    } | null;
    speech: string;
  }> {
    const resp = await apiClient.get(`/api/geo/telemetry/${encodeURIComponent(userId)}?hours=${hours}`);
    return resp.data;
  },

  async getGeoPeople(): Promise<Record<string, unknown>> {
    const resp = await apiClient.get('/api/geo/people');
    return resp.data;
  },

  async getGeoZones(): Promise<Record<string, unknown>> {
    const resp = await apiClient.get('/api/geo/zones');
    return resp.data;
  },

  /** The caller's waiting place reminders ("when I get home"); fired ones are gone. */
  async getPlaceReminders(): Promise<{ reminders: PlaceReminder[] }> {
    const resp = await apiClient.get('/api/geo/place-reminders');
    return resp.data;
  },

  /** Remind the caller when they arrive at or leave one of the family's places. */
  async addPlaceReminder(payload: {
    zone: string;
    on: 'arrive' | 'leave';
    title: string;
    url?: string;
  }): Promise<{ reminder: PlaceReminder }> {
    const resp = await apiClient.post('/api/geo/place-reminders', payload);
    return resp.data;
  },

  async removePlaceReminder(id: string): Promise<{ removed: string }> {
    const resp = await apiClient.delete(`/api/geo/place-reminders/${encodeURIComponent(id)}`);
    return resp.data;
  },

  // Family Circle & Vehicle Trips
  async getTrips(userId?: string): Promise<TripsResponse> {
    const resolved = resolveUserId(userId);
    // The whole history in one request (Wander lists it by day/week/month/
    // year); one size everywhere so callers sharing the cache agree.
    const params = new URLSearchParams({ limit: String(TRIPS_FETCH_LIMIT) });
    // No user named: the family's trips -- yours plus those of everyone who
    // shares their activity with you (geo applies that consent).
    if (resolved) params.set('user_id', resolved);
    else params.set('scope', 'family');
    const resp = await apiClient.get(`/api/geo/trips?${params.toString()}`);
    return resp.data;
  },

  /**
   * Last known GPS position for every user whose app has location sharing on.
   * Users with tracking off simply have no entry (or a stale one).
   */
  // Drive time by road from someone's latest position to a place (an HA zone,
  // "home" by default), subject to location-sharing consent.
  async getEta(userId: string, to = 'home'): Promise<EtaResponse> {
    const resp = await apiClient.get('/api/geo/eta', { params: { user_id: userId, to } });
    return resp.data;
  },

  // Family arrival / departure notices for the signed-in user (on by default).
  /** Which notice categories reach the signed-in person (docs/PUSH.md); absent means on. */
  async getNotificationPreferences(): Promise<NotificationPreferences> {
    const resp = await apiClient.get('/api/users/me/notification-preferences');
    return booleanMap(resp.data?.preferences ?? resp.data);
  },

  async setNotificationPreferences(preferences: NotificationPreferences): Promise<NotificationPreferences> {
    const resp = await apiClient.put('/api/users/me/notification-preferences', preferences);
    return booleanMap(resp.data?.preferences ?? resp.data);
  },

  /** Apps on the caller's phones that use Jarvis as their UnifiedPush distributor. */
  async listPushApps(): Promise<PushAppRegistration[]> {
    const resp = await apiClient.get('/api/push/up');
    const list = Array.isArray(resp.data) ? resp.data : resp.data?.registrations;
    return Array.isArray(list) ? list.filter((r): r is PushAppRegistration => typeof r?.app === 'string') : [];
  },

  async unregisterPushApp(registration: { app: string; token: string }): Promise<void> {
    await apiClient.post('/api/push/up/unregister', registration);
  },

  /** Mark a place (an HA zone) important and choose who hears about it; an empty list means everyone. */
  async setPlaceSettings(zoneId: string, settings: { important: boolean; notify: string[] }): Promise<{ zone: string; important: boolean; notify: string[] }> {
    const resp = await apiClient.put(`/api/geo/places/${encodeURIComponent(zoneId)}`, settings);
    return resp.data;
  },

  /** Tell the rest of the family you are heading home (with the drive time when known). */
  async sendOnMyWay(): Promise<{ status: string; sent: number; eta_s?: number | null }> {
    const resp = await apiClient.post('/api/family/on-my-way');
    return resp.data;
  },

  /** An urgent notice to everyone else in the family with your last position. */
  async sendSos(): Promise<{ status: string; sent: number; family: number; located: boolean }> {
    const resp = await apiClient.post('/api/family/sos', { confirm: true });
    return resp.data;
  },

  async getAllUserLocations(): Promise<Record<string, UserLiveLocation>> {
    const resp = await apiClient.get('/api/users/location/all');
    return resp.data || {};
  },

  async getTrip(tripId: string): Promise<Trip> {
    const resp = await apiClient.get(`/api/geo/trips/${encodeURIComponent(tripId)}`);
    return resp.data;
  },

  async getTripLocations(tripId: string): Promise<TripLocationsResponse> {
    const resp = await apiClient.get(`/api/geo/trips/${encodeURIComponent(tripId)}/locations`);
    return resp.data;
  },

  async updateTrip(tripId: string, update: TripUpdatePayload): Promise<Trip> {
    const resp = await apiClient.patch(`/api/geo/trips/${encodeURIComponent(tripId)}`, update);
    return resp.data;
  },

  async getLocationSuggestions(lat: number, lon: number): Promise<LocationSuggestionsResponse> {
    const resp = await apiClient.get('/api/geo/locations/suggestions', { params: { lat, lon } });
    return resp.data;
  },

  async getTripRoute(tripId: string): Promise<TripRouteResponse> {
    const resp = await apiClient.get(`/api/geo/trips/${encodeURIComponent(tripId)}/route`);
    return resp.data;
  },

  async shareTrip(tripId: string, riderIds: string[]): Promise<Trip> {
    const resp = await apiClient.patch(`/api/geo/trips/${encodeURIComponent(tripId)}/share`, { rider_ids: riderIds });
    return resp.data;
  },

  // Workouts (manual activity tracking)
  async startWorkout(activityType: string, userId?: string): Promise<{ status: string; workout: Workout }> {
    const resp = await apiClient.post('/api/geo/workouts/start', { activity_type: activityType, user_id: userId });
    return resp.data;
  },

  async stopWorkout(payload: { user_id?: string; notes?: string; steps?: number; distance_miles?: number }): Promise<{ status: string; workout: Workout }> {
    const resp = await apiClient.post('/api/geo/workouts/stop', payload);
    return resp.data;
  },

  /**
   * The caller's in-progress workout, or null when none is running.
   *
   * Needed because an active session is not in the workouts list: geo only
   * indexes it into history when it stops. The page used to look for a
   * `status === 'active'` entry that the server never emits, so a running
   * workout could never be recovered after a reload and the Stop button went
   * stale.
   */
  async getActiveWorkout(userId?: string): Promise<Workout | null> {
    const resp = await apiClient.get('/api/geo/workouts/active', {
      params: userId ? { user_id: userId } : undefined,
    });
    return (resp.data?.workout ?? null) as Workout | null;
  },

  async getWorkouts(userId?: string, limit = 20): Promise<WorkoutsResponse> {
    const params = new URLSearchParams({ limit: String(limit) });
    withUserParam(params, userId);
    const resp = await apiClient.get(`/api/geo/workouts?${params.toString()}`);
    return resp.data;
  },

  async getWorkoutRoute(workoutId: string): Promise<TripRouteResponse> {
    const resp = await apiClient.get(`/api/geo/workouts/${encodeURIComponent(workoutId)}/route`);
    return resp.data;
  },

  async dismissWorkout(workoutId: string): Promise<{ status: string; id: string }> {
    const resp = await apiClient.post(`/api/geo/workouts/${encodeURIComponent(workoutId)}/dismiss`);
    return resp.data;
  },

  // Daily steps (hardware pedometer)
  async getDailySteps(userId?: string, days = 7): Promise<StepsResponse> {
    const query = new URLSearchParams({ days: String(days) });
    withUserParam(query, userId);
    const resp = await apiClient.get(`/api/geo/steps?${query.toString()}`);
    return resp.data;
  },

  async getStepGoal(userId?: string): Promise<{ user_id: string; goal: number }> {
    const resolved = resolveUserId(userId);
    const query = resolved ? `?user_id=${encodeURIComponent(resolved)}` : '';
    const resp = await apiClient.get(`/api/geo/steps/goal${query}`);
    return resp.data;
  },

  /**
   * Pre-aggregated history for an event metric (workouts, distances).
   *
   * An empty day here means "no workout recorded", not "no reading" -- unlike
   * steps, where a missing day is a sensor that did not sync. The response
   * says which, via `empty`/`active_days` rather than `has_gaps`.
   *
   * A metric this install does not record is a 422 naming the metric and the
   * reason, never a confident zero.
   */
  async getMetricRanges(metric: string, userId?: string, range: StepRange = 'W'): Promise<MetricRangeResponse> {
    const query = new URLSearchParams({ metric, range });
    withUserParam(query, userId);
    const resp = await apiClient.get(`/api/geo/metrics/ranges?${query.toString()}`);
    return resp.data;
  },

  /** Which metrics this install records, and why the others are absent. */
  async getMetricCatalog(): Promise<MetricCatalogResponse> {
    const resp = await apiClient.get('/api/geo/metrics/catalog');
    return resp.data;
  },

  /**
   * The caller's personal event timeline, newest first and grouped by day.
   * Steps are deliberately not here: a day bucket is not an event.
   */
  async getTimeline(userId?: string, days = 30, limit = 60): Promise<TimelineResponse> {
    const query = new URLSearchParams({ days: String(days), limit: String(limit) });
    withUserParam(query, userId);
    const resp = await apiClient.get(`/api/geo/events?${query.toString()}`);
    return resp.data;
  },

  /**
   * Pre-aggregated step history for one range.
   *
   * The server folds the daily buckets so a month, quarter or year does not
   * mean shipping 365 days to the browser and aggregating per surface.
   * `range` is validated server-side; an unknown value is a 422 naming the
   * valid set, not a silent fallback.
   */
  async getStepRanges(userId?: string, range: StepRange = 'W'): Promise<StepRangeResponse> {
    const query = new URLSearchParams({ range });
    withUserParam(query, userId);
    const resp = await apiClient.get(`/api/geo/steps/ranges?${query.toString()}`);
    return resp.data;
  },

  async setStepGoal(goal: number, userId?: string): Promise<{ user_id: string; goal: number }> {
    const resp = await apiClient.put('/api/geo/steps/goal', { goal, user_id: userId });
    return resp.data;
  },

  // --- Health hub (profiles, sleep, check-ins, nutrition) -----------------

  async getHealthProfile(): Promise<HealthProfile> {
    const resp = await apiClient.get('/api/health/profile');
    return resp.data;
  },

  async updateHealthProfile(profile: Partial<HealthProfile>): Promise<HealthProfile> {
    const resp = await apiClient.put('/api/health/profile', profile);
    return resp.data;
  },

  /** One call for the Today screen. Activity comes from geo separately. */
  async getHealthSummary(day?: string): Promise<HealthSummary> {
    const query = day ? `?day=${encodeURIComponent(day)}` : '';
    const resp = await apiClient.get(`/api/health/summary${query}`);
    return resp.data;
  },

  /** The seven days ending on `end` (today by default) against the week before. */
  async getHealthWeekly(end?: string): Promise<HealthWeekly> {
    const resp = await apiClient.get('/api/health/weekly', { params: end ? { end } : {} });
    return resp.data;
  },

  /** The family's challenges for the week `day` falls in, with live standings. */
  async getHealthChallenges(day?: string): Promise<HealthChallengeWeek> {
    const resp = await apiClient.get('/api/health/challenges', { params: day ? { day } : {} });
    return resp.data;
  },

  async createHealthChallenge(body: {
    kind: HealthChallengeKind;
    day: string;
    target_days: number;
  }): Promise<HealthChallenge> {
    const resp = await apiClient.post('/api/health/challenges', body);
    return resp.data;
  },

  async joinHealthChallenge(id: number): Promise<HealthChallenge> {
    const resp = await apiClient.post(`/api/health/challenges/${id}/join`);
    return resp.data;
  },

  async leaveHealthChallenge(id: number): Promise<HealthChallenge> {
    const resp = await apiClient.delete(`/api/health/challenges/${id}/join`);
    return resp.data;
  },

  async deleteHealthChallenge(id: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/health/challenges/${id}`);
    return resp.data;
  },

  async getHealthMetrics(metric: string, days = 30): Promise<HealthMetricHistory> {
    const resp = await apiClient.get(
      `/api/health/metrics/${encodeURIComponent(metric)}?days=${days}`,
    );
    return resp.data;
  },

  async addHealthMetrics(
    samples: Array<{
      metric: string;
      value: number;
      unit: string;
      recorded_at?: string;
      source?: string;
      external_id?: string;
      metadata?: Record<string, unknown>;
    }>,
  ): Promise<{ accepted: number; duplicates: number; samples: HealthMetricSample[] }> {
    const resp = await apiClient.post('/api/health/metrics', { samples });
    return resp.data;
  },

  async deleteHealthMetric(sampleId: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/health/metrics/${sampleId}`);
    return resp.data;
  },

  async getSleep(days = 14): Promise<{ user_id: string; sessions: SleepSession[] }> {
    const resp = await apiClient.get(`/api/health/sleep?days=${days}`);
    return resp.data;
  },

  async logSleep(body: {
    night: string;
    bedtime?: string | null;
    wake_time?: string | null;
    duration_minutes?: number | null;
    efficiency?: number | null;
    quality?: number | null;
    note?: string;
    source?: string;
  }): Promise<SleepSession & { duplicate: boolean }> {
    const resp = await apiClient.post('/api/health/sleep', body);
    return resp.data;
  },

  /** Correct a saved night in place. */
  async updateSleep(sleepId: number, body: {
    night: string;
    bedtime?: string | null;
    wake_time?: string | null;
    duration_minutes?: number | null;
    efficiency?: number | null;
    quality?: number | null;
    note?: string;
    source?: string;
  }): Promise<SleepSession> {
    const resp = await apiClient.put(`/api/health/sleep/${sleepId}`, body);
    return resp.data;
  },

  /** Sleep for `night`, estimated from when this person's phones and tablets were in use. */
  async getSleepEstimate(night: string): Promise<{ estimate: SleepEstimate | null; devices: number }> {
    const resp = await apiClient.get('/api/health/sleep/estimate', { params: { night } });
    return resp.data;
  },

  async deleteSleep(sleepId: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/health/sleep/${sleepId}`);
    return resp.data;
  },

  async getCheckins(days = 14): Promise<{ user_id: string; checkins: HealthCheckin[] }> {
    const resp = await apiClient.get(`/api/health/checkins?days=${days}`);
    return resp.data;
  },

  async getCheckin(day: string): Promise<{ checkin: HealthCheckin | null }> {
    const resp = await apiClient.get(`/api/health/checkins/${encodeURIComponent(day)}`);
    return resp.data;
  },

  async saveCheckin(
    day: string,
    body: { energy?: number; mood?: number; soreness?: number; stress?: number; note?: string },
  ): Promise<HealthCheckin> {
    const resp = await apiClient.put(`/api/health/checkins/${encodeURIComponent(day)}`, body);
    return resp.data;
  },

  async getNutritionDay(day: string): Promise<NutritionDay> {
    const resp = await apiClient.get(`/api/health/nutrition/day?day=${encodeURIComponent(day)}`);
    return resp.data;
  },

  async getFoodEntries(day: string): Promise<{ user_id: string; entries: FoodEntry[] }> {
    const resp = await apiClient.get(`/api/health/food?day=${encodeURIComponent(day)}`);
    return resp.data;
  },

  async addFood(
    body: Partial<FoodEntry> & { day: string; name: string; meal: FoodEntry['meal'] },
  ): Promise<FoodEntry & { duplicate: boolean }> {
    const resp = await apiClient.post('/api/health/food', body);
    return resp.data;
  },

  async deleteFood(entryId: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/health/food/${entryId}`);
    return resp.data;
  },

  /** USDA seed + OpenFoodFacts search; results are per 100 g plus portion options. */
  async searchFood(
    q: string,
    limit = 10,
  ): Promise<{
    query: string;
    sources: string[];
    offline_sources: string[];
    seed: { present: boolean; results: number };
    results: FoodSearchItem[];
  }> {
    const params = new URLSearchParams({ q, limit: String(limit) });
    const resp = await apiClient.get(`/api/health/food/search?${params.toString()}`);
    return resp.data;
  },

  /** A nutrition-label photo read by the vision OCR into a draft; nothing is saved. */
  async readFoodLabelPhoto(photo: Blob): Promise<FoodLabelDraft> {
    const form = new FormData();
    form.append('image', photo, 'label.jpg');
    const resp = await apiClient.post('/api/health/food/photo', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // The vision model runs locally and can take a minute or two.
      timeout: 300_000,
    });
    return resp.data;
  },

  async lookupBarcode(code: string): Promise<FoodSearchItem> {
    const resp = await apiClient.get(`/api/health/food/barcode/${encodeURIComponent(code)}`);
    return resp.data;
  },

  /**
   * An **estimate** of a recipe's nutrition from the USDA seed, one line per
   * ingredient. Used when a recipe states no nutrition of its own; the caller
   * must label it as an estimate, and `unknown` ingredients carry no number.
   */
  async estimateRecipeNutrition(ingredients: string[]): Promise<RecipeEstimate> {
    const resp = await apiClient.post(
      '/api/health/food/estimate',
      { ingredients },
      { timeout: 30_000 },
    );
    return resp.data;
  },

  async getHealthShares(): Promise<{ owner: string; shares: HealthShare[] }> {
    const resp = await apiClient.get('/api/health/shares');
    return resp.data;
  },

  async updateHealthShare(body: {
    viewer: string;
    scope: HealthShare['scope'];
    enabled: boolean;
  }): Promise<{ owner: string; viewer: string; scope: string; enabled: boolean }> {
    const resp = await apiClient.put('/api/health/shares', body);
    return resp.data;
  },

  async getHealthFamily(day?: string): Promise<{
    viewer: string;
    day: string;
    people: HealthFamilyPerson[];
  }> {
    const query = day ? `?day=${encodeURIComponent(day)}` : '';
    const resp = await apiClient.get(`/api/health/family${query}`);
    return resp.data;
  },

  // --- Cycle tracking (opt-in, private, owner-only by default) -----------

  async getHealthCycle(day?: string): Promise<HealthCycleState> {
    const query = day ? `?day=${encodeURIComponent(day)}` : '';
    const resp = await apiClient.get(`/api/health/cycle${query}`);
    return resp.data;
  },

  async updateHealthCycleSettings(body: {
    enabled?: boolean;
    average_cycle_length?: number | null;
    average_period_length?: number | null;
    guardian_username?: string | null;
  }): Promise<HealthCycleState> {
    const resp = await apiClient.put('/api/health/cycle/settings', body);
    return resp.data;
  },

  async addHealthCyclePeriod(body: {
    start_day: string;
    end_day?: string | null;
    flow?: 'spotting' | 'light' | 'medium' | 'heavy';
    note?: string;
  }): Promise<{ period: HealthCyclePeriod; summary: HealthCycleSummary | null }> {
    const resp = await apiClient.post('/api/health/cycle/periods', body);
    return resp.data;
  },

  async deleteHealthCyclePeriod(periodId: number): Promise<void> {
    await apiClient.delete(`/api/health/cycle/periods/${periodId}`);
  },

  async getHealthCycleLog(day: string): Promise<{ day: string; log: HealthCycleLog | null }> {
    const resp = await apiClient.get(`/api/health/cycle/logs/${encodeURIComponent(day)}`);
    return resp.data;
  },

  async saveHealthCycleLog(
    day: string,
    body: {
      symptoms?: string[];
      mood?: number | null;
      energy?: number | null;
      bbt_c?: number | null;
      ovulation_test?: 'negative' | 'positive' | 'inconclusive' | null;
      note?: string;
    },
  ): Promise<{ log: HealthCycleLog }> {
    const resp = await apiClient.put(`/api/health/cycle/logs/${encodeURIComponent(day)}`, body);
    return resp.data;
  },

  async deleteHealthCycleLog(day: string): Promise<void> {
    await apiClient.delete(`/api/health/cycle/logs/${encodeURIComponent(day)}`);
  },

  /** The week's active minutes from steps, workouts, heart rate and device totals. */
  async getHealthActiveMinutes(day?: string): Promise<HealthActiveMinutes> {
    const resp = await apiClient.get('/api/health/active-minutes', { params: day ? { day } : {} });
    return resp.data;
  },

  async getHealthCheers(day?: string): Promise<HealthCheersDay> {
    const resp = await apiClient.get('/api/health/cheers', { params: day ? { day } : {} });
    return resp.data;
  },

  async sendHealthCheer(body: { to_user: string; kind: string; day: string }): Promise<HealthCheer & { duplicate: boolean }> {
    const resp = await apiClient.post('/api/health/cheers', body);
    return resp.data;
  },

  async getHealthGoals(day?: string): Promise<HealthGoalsWeek> {
    const resp = await apiClient.get('/api/health/goals', { params: day ? { day } : {} });
    return resp.data;
  },

  async createHealthGoal(body: { kind: HealthChallengeKind; day: string; target_days: number }): Promise<HealthGoalsWeek> {
    const resp = await apiClient.post('/api/health/goals', body);
    return resp.data;
  },

  async deleteHealthGoal(id: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/health/goals/${id}`);
    return resp.data;
  },

  async getHealthStreaks(day?: string): Promise<HealthStreaks> {
    const resp = await apiClient.get('/api/health/streaks', { params: day ? { day } : {} });
    return resp.data;
  },

  /** The running fast and the last week's (adults only; `allowed: false` for a minor). */
  async getHealthFasting(): Promise<HealthFastingState> {
    const resp = await apiClient.get('/api/health/fasting');
    return resp.data;
  },

  async startHealthFast(body: { started_at?: string; target_hours?: number | null }): Promise<HealthFast> {
    const resp = await apiClient.post('/api/health/fasting/start', body);
    return resp.data;
  },

  async stopHealthFast(body: { ended_at?: string } = {}): Promise<HealthFast> {
    const resp = await apiClient.post('/api/health/fasting/stop', body);
    return resp.data;
  },

  async deleteHealthFast(id: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/health/fasting/${id}`);
    return resp.data;
  },

  async exportHealth(): Promise<Record<string, unknown>> {
    const resp = await apiClient.get('/api/health/export');
    return resp.data;
  },

  async getGoals(userId?: string): Promise<{ user_id: string; goals: ActivityGoals }> {
    const resolved = resolveUserId(userId);
    const query = resolved ? `?user_id=${encodeURIComponent(resolved)}` : '';
    const resp = await apiClient.get(`/api/geo/goals${query}`);
    return resp.data;
  },

  async updateGoals(
    goals: Partial<ActivityGoals>,
    userId?: string
  ): Promise<{ user_id: string; goals: ActivityGoals }> {
    const resp = await apiClient.put('/api/geo/goals', { goals, user_id: userId });
    return resp.data;
  },

  /** Queue a song; it is written on the audio server and polled with getSong. */
  async startSong(payload: { prompt: string; duration_s: number; structure: string; seed?: number }): Promise<SongJob> {
    const resp = await apiClient.post('/api/music/songs', payload, { timeout: 30_000 });
    return resp.data.job;
  },

  async getSong(id: string): Promise<SongJob> {
    const resp = await apiClient.get(`/api/music/songs/${encodeURIComponent(id)}`, { timeout: 30_000 });
    return resp.data.job;
  },

  async cancelSong(id: string): Promise<SongJob> {
    const resp = await apiClient.delete(`/api/music/songs/${encodeURIComponent(id)}`);
    return resp.data.job;
  },

  async joinTalkCall(token: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/call', { token, as_user });
    return resp.data;
  },

  async leaveTalkCall(token: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/call', { token, leave: true, as_user });
    return resp.data;
  },

  /** The family calls that currently have someone in them. */
  async getActiveCalls(): Promise<ActiveCall[]> {
    const resp = await apiClient.get('/api/communication/calls');
    return resp.data.calls;
  },

  async getStars(userId?: string): Promise<StarsResponse> {
    const query = new URLSearchParams();
    withUserParam(query, userId);
    const resp = await apiClient.get(`/api/geo/stars?${query.toString()}`);
    return resp.data;
  },

  async grantStars(payload: {
    user_id: string;
    stars: number;
    reason?: string;
    note?: string;
  }): Promise<StarGrant> {
    const resp = await apiClient.post('/api/geo/stars', payload);
    return resp.data;
  },

  /**
   * Admin grant that mirrors into the target's Skylight account.
   *
   * The target is the path, never a body field: a body `user_id` could disagree
   * with the URL and there would be no way to tell which one the stars went to.
   * The ledger write and the mirror are reported apart, so an admin surface can
   * say "recorded, mirror failed" instead of guessing.
   */
  async grantStarsForUser(
    userId: string,
    payload: {
      stars: number;
      reason?: string;
      note?: string;
      mirror_to_skylight?: boolean;
    },
  ): Promise<AdminStarGrantResponse> {
    const resp = await apiClient.post(
      `/api/admin/users/${encodeURIComponent(userId)}/stars`,
      payload,
    );
    return resp.data;
  },

  async getAchievements(userId?: string, days = 30): Promise<AchievementsResponse> {
    const query = new URLSearchParams({ days: String(days) });
    withUserParam(query, userId);
    const resp = await apiClient.get(`/api/geo/achievements?${query.toString()}`);
    return resp.data;
  },

  // Activity trends: reading data never generates an analysis.
  async getActivityTrends(userId?: string, days = 7, refresh = false): Promise<ActivityTrendsResponse> {
    const params = new URLSearchParams({ days: String(days) });
    withUserParam(params, userId);
    if (refresh) params.set('refresh', 'true');
    const resp = await apiClient.get(`/api/geo/trends/activity?${params.toString()}`);
    return resp.data;
  },

  // Explicit opt-in analysis. Only call this when the user asks for it.
  async analyzeActivityTrends(userId?: string, days = 7, refresh = false): Promise<ActivityTrendsResponse> {
    const params = new URLSearchParams({ days: String(days) });
    withUserParam(params, userId);
    if (refresh) params.set('refresh', 'true');
    const resp = await apiClient.post(`/api/geo/trends/activity/analyze?${params.toString()}`);
    return resp.data;
  },

  // Opt-in shared activity. The server enforces audiences; the client only
  // ever requests what the viewer is allowed to see.
  async getActivitySummary(window: ActivityWindow = 'week'): Promise<ActivitySummaryResponse> {
    const resp = await apiClient.get('/api/geo/activity/summary', { params: { window } });
    return resp.data;
  },

  async getActivityFeed(window: ActivityWindow = 'week'): Promise<ActivityFeedResponse> {
    const resp = await apiClient.get('/api/geo/activity/feed', { params: { window } });
    return resp.data;
  },

  // Speech-to-Text
  async transcribeAudio(audioBlob: Blob, model = 'base', language = 'en'): Promise<{ status: string; transcript: string }> {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.wav');
    formData.append('model', model);
    formData.append('language', language);
    const resp = await apiClient.post('/api/stt/transcribe', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return resp.data;
  },

  // Voice Commands
  async executeVoiceCommand(transcript: string, userId: string, entityId?: string): Promise<{ status: string; message: string; transcript?: string }> {
    const resp = await apiClient.post('/api/voice/command', {
      transcript,
      user_id: userId,
      entity_id: entityId,
    });
    return resp.data;
  },

  async testConnection(service: string, config: Record<string, unknown>): Promise<{ status: 'SUCCESS' | 'ERROR'; message?: string }> {
    const resp = await apiClient.post('/api/auth/test-connection', { service, config });
    return resp.data;
  },

  async getWorkspaces(): Promise<Workspace[]> {
    const resp = await apiClient.get<WorkspaceListResponse>('/api/workspaces');
    return normalizeWorkspaces(resp.data);
  },

  async createWorkspace(data: Partial<Workspace> & { id: string }): Promise<Workspace> {
    const resp = await apiClient.post('/api/workspaces', data);
    return resp.data.workspace;
  },

  /**
   * Every workspace session saved for this account, keyed by workspace id.
   *
   * Typed loosely on purpose: the session blob is validated field by field in
   * workspaceSession.ts, and the server is not trusted to hand back a shape it
   * was given.
   */
  async getWorkspaceSessions(): Promise<{ sessions: Record<string, unknown> }> {
    const resp = await apiClient.get<{ sessions: Record<string, unknown> }>(
      '/api/workspaces/sessions',
    );
    return { sessions: resp.data?.sessions ?? {} };
  },

  async putWorkspaceSession(workspaceId: string, session: Record<string, unknown>): Promise<void> {
    await apiClient.put(
      `/api/workspaces/sessions/${encodeURIComponent(workspaceId)}`,
      session,
    );
  },

  async updateWorkspace(id: string, data: Partial<Workspace>): Promise<Workspace> {
    const resp = await apiClient.patch(`/api/workspaces/${id}`, data);
    return resp.data.workspace;
  },

  async deleteWorkspace(id: string, deleteArtifacts?: boolean): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/workspaces/${id}`, {
      data: deleteArtifacts === undefined ? undefined : { delete_artifacts: deleteArtifacts },
    });
    return resp.data;
  },

  async pullWorkspace(id: string, branch?: string): Promise<{ status: string; message?: string; branch: string; recovered?: boolean; recovery_note?: string }> {
    const resp = await apiClient.post('/api/workspaces/git/pull', { workspace_id: id, branch });
    return resp.data;
  },

  async revertWorkspace(id: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/workspaces/git/revert', { workspace_id: id });
    return resp.data;
  },

  async getAPIKeys(): Promise<APIKey[]> {
    const resp = await apiClient.get('/api/users/me/keys');
    return resp.data;
  },

  async generateAPIKey(label: string): Promise<APIKey> {
    const resp = await apiClient.post('/api/users/me/keys', { label });
    return {
      ...resp.data,
      prefix: resp.data.prefix ?? String(resp.data.key || '').slice(0, 8),
    };
  },

  async revokeAPIKey(keyId: string | number): Promise<{ success: boolean }> {
    const resp = await apiClient.delete(`/api/users/me/keys/${keyId}`);
    return resp.data;
  },

  async getDevices(): Promise<DeviceAssignment[]> {
    const resp = await apiClient.get('/api/users/devices');
    return resp.data;
  },

  async getRoles(): Promise<RoleCatalog> {
    const resp = await apiClient.get('/api/users/roles');
    return resp.data;
  },

  async saveRole(role: { id?: number; name: string; description: string; permissions: string[] }): Promise<Role> {
    const body = { name: role.name, description: role.description, permissions: role.permissions };
    const resp = role.id
      ? await apiClient.patch(`/api/users/roles/${role.id}`, body)
      : await apiClient.post('/api/users/roles', body);
    return resp.data;
  },

  async deleteRole(id: number): Promise<void> {
    await apiClient.delete(`/api/users/roles/${id}`);
  },

  /** Replace the roles a user holds. */
  async setUserRoles(username: string, roleIds: number[]): Promise<UserProfile> {
    const resp = await apiClient.put(`/api/users/${encodeURIComponent(username)}/roles`, { role_ids: roleIds });
    return normalizeUser(resp.data);
  },

  /** Let one more user control a device; whoever already has it keeps it. */
  async updateDeviceAssignment(assignment: { username: string; device_id: string }): Promise<DeviceAssignment> {
    const resp = await apiClient.post('/api/users/devices', assignment);
    return resp.data;
  },

  /** Take a device away from one user, or from everyone when no user is named. */
  async deleteDeviceAssignment(deviceId: string, username?: string): Promise<{ status?: string; success?: boolean }> {
    const resp = await apiClient.delete(`/api/devices/${encodeURIComponent(deviceId)}`, {
      params: username ? { username } : undefined,
    });
    return resp.data;
  },

  /** Every entity locked against normal users, with its permit list. Admins only. */
  async getEntityProtections(): Promise<EntityProtection[]> {
    const resp = await apiClient.get('/api/entity-protection');
    return resp.data;
  },

  /**
   * Lock an entity for everyone but the permit list, or release the lock.
   *
   * Admins only (Identity enforces). `protected: false` removes the lock
   * entirely; the entity reverts to plain device-assignment rules. A refusal
   * is re-thrown with Identity's own wording, because "you asked for a permit
   * for a user that does not exist" is the entire point of the check.
   */
  async setEntityProtection(
    entityId: string,
    body: { protected: boolean; permitted_usernames: string[]; note?: string | null },
  ): Promise<EntityProtection> {
    try {
      const resp = await apiClient.put(`/api/entity-protection/${encodeURIComponent(entityId)}`, {
        protected: body.protected,
        permitted_usernames: body.permitted_usernames,
        note: body.note ?? null,
      });
      return resp.data;
    } catch (error) {
      return rethrowWithServerDetail(error, `Failed to update protection for ${entityId}`);
    }
  },

  async syncDiscovery(): Promise<{ status: string; entities_count: number }> {
    const resp = await apiClient.post('/api/discovery/sync', {});
    return resp.data;
  },

  async getEntities(): Promise<{ entity_id: string; friendly_name: string; state: string; domain: string }[]> {
    const resp = await apiClient.get('/api/entities');
    return resp.data.entities || [];
  },

  async getTimers(): Promise<TimerRecord[]> {
    const resp = await apiClient.get('/api/communication/timers');
    return resp.data;
  },

  async getTimerDevices(): Promise<TimerDevicesResponse> {
    const resp = await apiClient.get('/api/communication/timers/devices');
    return resp.data;
  },

  async createTimer(payload: {
    title: string;
    duration_str?: string;
    time_str?: string;
    type?: 'timer' | 'alarm' | 'reminder';
    recurrence?: string;
    target_device?: string;
    target_devices?: string[];
    scope?: 'user' | 'household' | 'family';
    emoji?: string;
    sound?: string;
    tts_enabled?: boolean;
    esphome_script?: string;
    flash_screen?: boolean;
    /** The app page the notice opens, e.g. a note's `/notes?note=…` (in-app paths only). */
    url?: string;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/timers', payload);
    return resp.data;
  },

  async deleteTimer(title: string, type: 'timer' | 'alarm' | 'reminder' = 'timer', id?: string): Promise<ExecutionResponse> {
    const data: { title: string; type: 'timer' | 'alarm' | 'reminder'; id?: string } = { title, type };
    if (id) data.id = id;
    const resp = await apiClient.delete('/api/communication/timers', { data });
    return resp.data;
  },

  async timerAction(
    action: 'pause' | 'resume' | 'stop' | 'reset',
    payload: { title: string; type?: 'timer' | 'alarm' | 'reminder'; id?: string }
  ): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/timers', { action, ...payload });
    return resp.data;
  },

  async getCalendarList(): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/calendar/calendars');
    return resp.data;
  },

  /** Events in `range` (ISO dates, end exclusive; the server caps it at 120 days), or the next week. */
  async getCalendarEvents(calendar_name?: string, integration?: string, range?: { start: string; end: string }): Promise<ExecutionResponse> {
    const params: Record<string, string> = {};
    if (calendar_name) params.calendar_name = calendar_name;
    if (integration) params.integration = integration;
    if (range) {
      params.start = range.start;
      params.end = range.end;
    }
    const resp = await apiClient.get('/api/communication/calendar/events', { params });
    return resp.data;
  },

  async addCalendarEvent(payload: { summary: string; start_time: string; calendar_name?: string; integration?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/calendar/events', payload);
    return resp.data;
  },

  async updateCalendarEvent(payload: { action: 'update'; event_id?: string; integration?: string; query?: string; summary?: string; start_time?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.put('/api/communication/calendar/events', payload);
    return resp.data;
  },

  async deleteCalendarEvent(payload: { action: 'delete'; event_id?: string; integration?: string; query?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.delete('/api/communication/calendar/events', { data: payload });
    return resp.data;
  },

  async getCalendarSettings(): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/calendar/settings');
    return resp.data;
  },

  async updateCalendarSettings(payload: { default?: string; disabled?: string[]; priority?: Record<string, number>; ical_urls?: string[]; people?: CalendarPerson[]; reminder_minutes?: number; timezone?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.put('/api/calendar/settings', payload);
    return resp.data;
  },

  async createNote(payload: { title: string; content?: string; category?: string; storage?: string; as_user?: 'admin' }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/create', payload, { timeout: 60_000 });
    return resp.data;
  },

  async readNote(title: string, storage?: string, path?: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/read', { title, storage, path, as_user }, { timeout: 60_000 });
    return resp.data;
  },

  async appendNote(payload: { title: string; content: string; storage?: string; path?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/append', payload, { timeout: 60_000 });
    return resp.data;
  },

  /**
   * Full replace: the editor's Save. Append would duplicate the body.
   * `if_match` is the version `readNote` returned (`detail.etag`); a note
   * changed elsewhere since then comes back as a FAILURE with
   * `detail.conflict` instead of being overwritten.
   */
  async writeNote(payload: {
    title: string;
    content: string;
    category?: string;
    storage?: string;
    path?: string;
    as_user?: 'admin';
    if_match?: string;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/write', payload, { timeout: 60_000 });
    return resp.data;
  },

  /** Toggle a checklist item ("- [ ] x" <-> "- [x] x") inside a note. */
  async checkOffNote(payload: {
    title: string;
    item: string;
    storage?: string;
    path?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/check_off', payload, { timeout: 60_000 });
    return resp.data;
  },

  /**
   * Delete a note, and the attachment folders it links to. `attachmentsTo`
   * (a rename's old copy) hands those to the new path instead.
   */
  async deleteNote(
    title: string,
    storage?: string,
    path?: string,
    as_user?: 'admin',
    attachmentsTo?: string,
  ): Promise<ExecutionResponse> {
    const resp = await apiClient.post(
      '/api/communication/notes/delete',
      { title, storage, path, as_user, ...(attachmentsTo ? { attachments_to: attachmentsTo } : {}) },
      { timeout: 60_000 },
    );
    return resp.data;
  },

  /**
   * A saved note's attachment, beside it in Nextcloud as Nextcloud Notes
   * keeps them: `upload` (base64 `data`; `detail.link` is the relative link
   * to put in the note), `read` (`detail.data` base64 and `detail.mime`) or
   * `delete` one by its `attachment` link.
   */
  async noteAttachment(payload: {
    action: 'upload' | 'read' | 'delete';
    path: string;
    attachment?: string;
    filename?: string;
    data?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post(
      '/api/communication/notes/attachment',
      { storage: 'nextcloud', ...payload },
      { timeout: 120_000 },
    );
    return resp.data;
  },

  // `previews` asks for the newest notes' text in the same reply (on each
  // note's `content`), so a page of cards is one request, not one per card.
  // Listing walks Nextcloud folders over WebDAV, which can outlast 15s.
  async listNotes(payload: { storage?: string; directories?: string[]; as_user?: 'admin'; previews?: boolean } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/list', payload, { timeout: 60_000 });
    return resp.data;
  },

  /** Read the shared notes index (pin, colour, archive origin) from /Notes. */
  async getNotesMeta(payload: { storage?: string; as_user?: 'admin' } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/meta', payload, { timeout: 60_000 });
    return resp.data;
  },

  /** Replace the shared notes index. One writer, so callers read-modify-write. */
  async writeNotesMeta(payload: {
    meta: Record<string, unknown>;
    storage?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/meta', { write: true, ...payload }, { timeout: 60_000 });
    return resp.data;
  },

  /** WebDAV MOVE: archive, trash, restore or rename without copy+delete. */
  async moveNote(payload: {
    path: string;
    destination: string;
    storage?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/move', payload, { timeout: 60_000 });
    return resp.data;
  },

  /** Copy a note to "<title> (copy).md", never overwriting an existing file. */
  async duplicateNote(payload: {
    path: string;
    title?: string;
    storage?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/duplicate', payload, { timeout: 60_000 });
    return resp.data;
  },

  /**
   * Nextcloud's earlier versions of one note: `list` (detail.versions, newest
   * first), `read` one (its text in `message`) or `restore` one over the note.
   */
  async noteVersions(payload: {
    path: string;
    action?: 'list' | 'read' | 'restore';
    version?: string;
    storage?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/versions', payload, { timeout: 60_000 });
    return resp.data;
  },

  /**
   * Nextcloud sharing for one note: `list` who has it (detail.shares),
   * `people` it could be shared with (detail.people), `add` a person
   * (read only, or `can_edit`), `remove` a share by id.
   */
  async noteShare(payload: {
    action: 'list' | 'people' | 'add' | 'remove';
    path?: string;
    share_with?: string;
    can_edit?: boolean;
    share_id?: string;
    search?: string;
    storage?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/share', payload, { timeout: 60_000 });
    return resp.data;
  },

  /** Permanently delete everything in Notes/Trash. */
  async emptyNotesTrash(payload: { storage?: string; as_user?: 'admin' } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/empty_trash', payload, { timeout: 60_000 });
    return resp.data;
  },

  async syncNotesRag(payload: { storage?: string; directories?: string[] } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/sync_rag', payload, { timeout: 60_000 });
    return resp.data;
  },

  async postActivity(payload: {
    feature: string;
    item?: string;
    event?: string;
    duration_s?: number;
    detail?: Record<string, unknown>;
  }): Promise<ExecutionResponse> {
    // The activity beacon: a light page view for the family profile.
    const resp = await apiClient.post('/api/activity', payload);
    return resp.data;
  },

  async importRecipe(url: string): Promise<ExecutionResponse> {
    // The server fetches the page (a slow site can take a while); 60s like notes.
    const resp = await apiClient.post('/api/recipes/import', { url }, { timeout: 60_000 });
    return resp.data;
  },

  async searchRecipes(query: string): Promise<ExecutionResponse> {
    // Recipe search: the offline index first, then TheMealDB fills the page.
    const resp = await apiClient.post('/api/recipes/search', { query }, { timeout: 60_000 });
    return resp.data;
  },

  async fetchRecipe(id: number): Promise<ExecutionResponse> {
    // One full recipe out of the offline index (search cards carry no body).
    const resp = await apiClient.post('/api/recipes/fetch', { id }, { timeout: 60_000 });
    return resp.data;
  },

  /**
   * The source URLs already imported into Recipes/ (the duplicate-import
   * guard). Reads every recipe note server-side; a repeat call is cached.
   */
  async recipeSources(as_user?: 'admin'): Promise<string[]> {
    const resp = await apiClient.post('/api/recipes/sources', { as_user }, { timeout: 60_000 });
    const sources = (resp.data?.detail?.sources ?? []) as unknown[];
    return sources.filter((source): source is string => typeof source === 'string');
  },

  /** A recipe photo (or handwriting) read by the vision OCR into a draft; nothing is saved. */
  async readRecipePhoto(photo: Blob): Promise<RecipePhotoDraft> {
    const form = new FormData();
    form.append('image', photo, 'recipe.jpg');
    const resp = await apiClient.post('/api/recipes/photo', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // The vision model runs locally and can take a minute or two.
      timeout: 300_000,
    });
    return resp.data;
  },

  async sendAnnouncement(payload: { entity_id: string; message: string; volume?: number }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/announcements', payload);
    return resp.data;
  },

  /** Start a family game in a room: trivia, memory, or the Bible quiz. */
  async startTalkGame(token: string, kind: 'trivia' | 'memory' | 'bible'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/game', {
      token,
      game_command: 'start',
      game_kind: kind,
    });
    return resp.data;
  },

  async getTalkConversations(): Promise<ExecutionResponse> {
    // Talk can take 20s+ on a cold Nextcloud worker. The global 15s client
    // timeout used to abort the request, leaving the Family chat list empty.
    const resp = await apiClient.get('/api/communication/talk/conversations', { timeout: 45_000 });
    return resp.data;
  },

  async createTalkGroup(name: string, participants: string[]): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/conversations/group', { name, participants });
    return resp.data;
  },

  async deleteTalkConversation(token: string): Promise<ExecutionResponse> {
    const resp = await apiClient.delete('/api/communication/talk/conversations', { params: { token } });
    return resp.data;
  },

  async getTalkParticipants(token: string): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/talk/participants', { params: { token } });
    return resp.data;
  },

  async addTalkParticipant(token: string, user: string): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/participants', { token, user });
    return resp.data;
  },

  async removeTalkParticipant(token: string, attendeeId: number): Promise<ExecutionResponse> {
    const resp = await apiClient.delete('/api/communication/talk/participants', { params: { token, attendee_id: attendeeId } });
    return resp.data;
  },

  async searchTalkPeople(search: string, token?: string): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/talk/people', { params: { search, token } });
    return resp.data;
  },

  async openTalkConversation(payload: { token?: string; target_user?: string; as_user?: 'admin' }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/conversations/open', payload);
    return resp.data;
  },

  async getTalkMessages(token: string, limit = 50): Promise<ExecutionResponse> {
    const resp = await apiClient.get(`/api/communication/talk/messages?token=${encodeURIComponent(token)}&limit=${limit}`);
    return resp.data;
  },

  /** Long poll: answers once a message newer than `lastId` exists, or empty after `wait` s. */
  async waitTalkMessages(token: string, lastId: number, wait = 25, as_user?: 'admin', signal?: AbortSignal): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/talk/messages/wait', {
      params: { token, last_id: lastId, wait, ...(as_user ? { as_user } : {}) },
      // Longer than the hold, or the app would abandon every quiet wait.
      timeout: (wait + 20) * 1000,
      signal,
    });
    return resp.data;
  },

  async sendTalkMessage(payload: { token: string; message: string; as_user?: 'admin'; reply_to?: number }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/messages', payload);
    return resp.data;
  },

  async markTalkRead(token: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/read', { token, as_user });
    return resp.data;
  },

  async getTalkPolls(token: string): Promise<ExecutionResponse> {
    const resp = await apiClient.get(`/api/communication/talk/polls?token=${encodeURIComponent(token)}`);
    return resp.data;
  },

  async createTalkPoll(payload: {
    token: string;
    question: string;
    options: string[];
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/polls/create', payload);
    return resp.data;
  },

  async voteTalkPoll(payload: {
    token: string;
    poll_id: number;
    option_id: number;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/polls/vote', payload);
    return resp.data;
  },

  async getTalkReactions(token: string, messageId: number): Promise<ExecutionResponse> {
    const resp = await apiClient.get(
      `/api/communication/talk/reactions?token=${encodeURIComponent(token)}&message_id=${messageId}`
    );
    return resp.data;
  },

  async reactToTalkMessage(payload: {
    token: string;
    message_id: number;
    reaction: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/react', payload);
    return resp.data;
  },

  async sendTalkVoice(payload: {
    token: string;
    audio_base64: string;
    mime_type?: string;
    file_name?: string;
    caption?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/voice', payload);
    return resp.data;
  },

  /** Share a photo or file into a conversation (base64, up to 25 MB). */
  async sendTalkFile(payload: {
    token: string;
    file_base64: string;
    mime_type: string;
    file_name: string;
    caption?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/file', payload, { timeout: 180_000 });
    return resp.data;
  },

  /** A chat attachment's bytes (or its thumbnail), fetched as the caller. */
  async getTalkFileBlob(params: { path?: string; file_id?: string | number; preview?: boolean; size?: number }): Promise<Blob> {
    const query = new URLSearchParams();
    if (params.path) query.set('path', params.path);
    if (params.file_id !== undefined) query.set('file_id', String(params.file_id));
    if (params.preview) query.set('preview', '1');
    if (params.size) query.set('size', String(params.size));
    const resp = await apiClient.get(`/api/communication/talk/file?${query.toString()}`, { responseType: 'blob', timeout: 180_000 });
    return resp.data;
  },

  async editTalkMessage(payload: { token: string; message_id: number; message: string; as_user?: 'admin' }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/messages/edit', payload);
    return resp.data;
  },

  async deleteTalkMessage(payload: { token: string; message_id: number; as_user?: 'admin' }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/messages/delete', payload);
    return resp.data;
  },

  async getTalkMentions(token: string, search: string): Promise<ExecutionResponse> {
    const resp = await apiClient.get(
      `/api/communication/talk/mentions?token=${encodeURIComponent(token)}&search=${encodeURIComponent(search)}`,
    );
    return resp.data;
  },

  async getArcadeGames(sort?: ArcadeSort): Promise<ArcadeGamesResponse> {
    const resp = await apiClient.get('/api/arcade/games', { params: sort ? { sort } : undefined });
    return resp.data;
  },

  async setArcadeFeatured(slugs: string[]): Promise<ExecutionResponse> {
    const resp = await apiClient.put('/api/arcade/featured', { slugs });
    return resp.data;
  },

  async rateArcadeGame(slug: string, stars: number): Promise<{ success: boolean; rating?: { count: number; average: number } }> {
    const resp = await apiClient.post(`/api/arcade/games/${encodeURIComponent(slug)}/rate`, { stars });
    return resp.data;
  },

  async getArcadeScoreboard(): Promise<ArcadeScoreboardResponse> {
    const resp = await apiClient.get('/api/arcade/scoreboard');
    return resp.data;
  },

  async getArcadeHighscores(): Promise<ArcadeHighscoresResponse> {
    const resp = await apiClient.get('/api/arcade/highscores');
    return resp.data;
  },

  async submitArcadeScore(slug: string, score: number, captureId: string): Promise<{ success: boolean; score?: number; rank?: number }> {
    const resp = await apiClient.post(`/api/arcade/games/${encodeURIComponent(slug)}/scores`, {
      score,
      capture_id: captureId,
    });
    return resp.data;
  },

  async runSmokeTest(): Promise<SmokeTestResult> {
    const resp = await apiClient.post('/api/admin/tests/smoke');
    return resp.data;
  },

  async runUnitTests(): Promise<SmokeTestResult> {
    const resp = await apiClient.post('/api/admin/tests/unit');
    return resp.data;
  },

  async getStorageFiles(path: string): Promise<StorageEntry[]> {
    const resp = await apiClient.post('/api/storage/list', { path, recursive: false });
    return resp.data.entries || [];
  },

  async triggerIndexing(path: string, recursive = true): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/storage/index', { path, recursive });
    return resp.data;
  },

  async triggerIndexingForce(path: string, recursive = true): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/storage/index', { path, recursive, force: true });
    return resp.data;
  },

  // The gateway resolves the signed-in user's own provider credentials, so
  // only the kind is sent. (This used to post to /api/storage/index/full,
  // which no service serves.)
  async triggerFullIndex(provider: { kind: string }, options?: {
    path?: string;
    recursive?: boolean;
    force?: boolean;
  }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/storage/index', {
      provider_kind: provider.kind,
      path: options?.path ?? '/',
      recursive: options?.recursive ?? true,
      force: options?.force ?? false,
    });
    return resp.data;
  },

  async getRagStats(): Promise<RagStats> {
    const resp = await apiClient.get('/api/storage/stats');
    return resp.data;
  },

  async getStorageStatus(): Promise<StorageStatus> {
    const resp = await apiClient.get('/api/storage/status');
    return resp.data;
  },

  async listArtifacts(params: {
    kind?: string;
    workspace_id?: string;
    attached?: boolean;
    search?: string;
    include_deleted?: boolean;
    limit?: number;
    offset?: number;
  } = {}): Promise<ArtifactListResponse> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value));
    }
    const suffix = query.toString();
    const resp = await apiClient.get(`/api/artifacts${suffix ? `?${suffix}` : ''}`);
    return resp.data;
  },

  async getArtifact(id: string): Promise<Artifact> {
    const resp = await apiClient.get(`/api/artifacts/${encodeURIComponent(id)}`);
    return resp.data;
  },

  /** A short-lived signed URL; no login header is needed to fetch it. */
  async getArtifactDownloadUrl(id: string): Promise<{ url: string; expires_in: number; name: string }> {
    const resp = await apiClient.get(`/api/artifacts/${encodeURIComponent(id)}/download`);
    return resp.data;
  },

  async patchArtifact(id: string, payload: { name?: string; metadata?: Record<string, unknown>; is_attached?: boolean }): Promise<Artifact> {
    const resp = await apiClient.patch(`/api/artifacts/${encodeURIComponent(id)}`, payload);
    return resp.data;
  },

  async deleteArtifact(id: string, options: { hard?: boolean; confirmPhrase?: string } = {}): Promise<Artifact & { hard: boolean; restored?: boolean }> {
    const suffix = options.hard ? '?hard=true' : '';
    const resp = await apiClient.delete(`/api/artifacts/${encodeURIComponent(id)}${suffix}`, {
      data: options.hard ? { confirm_phrase: options.confirmPhrase ?? '' } : undefined,
    });
    return resp.data;
  },

  async restoreArtifact(id: string): Promise<Artifact> {
    const resp = await apiClient.post(`/api/artifacts/${encodeURIComponent(id)}/restore`);
    return resp.data;
  },

  async checkArtifactFit(size: number): Promise<ArtifactFit> {
    const resp = await apiClient.post('/api/artifacts/check-fit', { size });
    return resp.data;
  },

  async getStorageUsage(): Promise<StorageUsage> {
    const resp = await apiClient.get('/api/storage/usage');
    return resp.data;
  },

  async getSystemStorageUsage(): Promise<SystemStorageUsage> {
    const resp = await apiClient.get('/api/storage/usage/system');
    return resp.data;
  },

  async setStorageQuota(username: string, quotaBytes: number): Promise<{ username: string; quota_bytes: number }> {
    const resp = await apiClient.put(`/api/storage/quota/${encodeURIComponent(username)}`, { quota_bytes: quotaBytes });
    return resp.data;
  },

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async getCollectionDocs(collectionName: string, limit: number = 100): Promise<any> {
    const resp = await apiClient.get(`/api/storage/collection/${collectionName}?limit=${limit}`);
    return resp.data;
  },

  // The gateway purges the signed-in user's own rows (Jarvis and Nextcloud
  // usernames); it never takes a user from the body, so none is sent.
  async purgeRagCollection(collectionName: string, filter?: Record<string, unknown>): Promise<{ status: string; message: string; removed?: number }> {
    const resp = await apiClient.post(`/api/storage/purge/${collectionName}`, { filter });
    return resp.data;
  },

  async getRavenLearnings(limit: number = 200, sort: 'recent' | 'reuse' = 'recent'): Promise<RavenLearningsResponse> {
    const resp = await apiClient.get(`/api/storage/learning?limit=${limit}&sort=${sort}`);
    return resp.data;
  },

  async editRavenLearning(docId: string, payload: { content?: string; metadata?: Record<string, unknown> }): Promise<{ status: string; id: string }> {
    const resp = await apiClient.patch(`/api/storage/learning/${docId}`, payload);
    return resp.data;
  },

  async deleteRavenLearning(docId: string): Promise<{ status: string; id: string }> {
    const resp = await apiClient.delete(`/api/storage/learning/${docId}`);
    return resp.data;
  },

  async changePassword(newPassword: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/auth/change-password', { new_password: newPassword });
    const data = resp.data as { status?: string; message?: string; api_key?: string };
    // The server rotates the session key on a password change so every other
    // device is signed out. Keep this one signed in with the new key.
    if (data?.api_key) {
      await storageSet('jarvis_api_key', data.api_key);
    }
    return { status: data?.status ?? 'SUCCESS', message: data?.message ?? 'Password updated' };
  },

  async adminSetPassword(username: string, newPassword: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/users/${username}/password`, { new_password: newPassword });
    return resp.data;
  },

  async importNextcloudUsers(): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/auth/import/nextcloud');
    return resp.data;
  },

  async getRavenConfig(): Promise<RavenConfig> {
    const resp = await apiClient.get('/api/admin/raven/config');
    return resp.data;
  },

  updateRavenConfig: async (config: Partial<RavenConfig>): Promise<{ status: string }> => {
    const { data } = await apiClient.patch('/api/admin/raven/config', config);
    return data;
  },

  getRavenVoices: async (): Promise<{ status: string, voices: string[] }> => {
    const { data } = await apiClient.get('/api/admin/raven/tts/voices');
    return data;
  },

  downloadRavenModels: async (): Promise<{ status: string, results: string[] }> => {
    const { data } = await apiClient.post('/execute/tts/download');
    return data;
  },

  async getAdminRavenQueue(): Promise<RavenMission[]> {
    const resp = await apiClient.get('/api/admin/raven/queue');
    return resp.data;
  },

  async executeAdminRavenMission(id: number): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/admin/raven/queue/${id}/execute`);
    return resp.data;
  },

  async getUserMissions(): Promise<RavenMission[]> {
    const resp = await apiClient.get('/api/raven/missions');
    return resp.data;
  },

  async getRavenMission(id: number | string): Promise<RavenMission> {
    const resp = await apiClient.get(`/api/raven/missions/${id}`);
    return resp.data;
  },

  async createUserMission(query: string, priority = 1): Promise<{ status: string; mission: RavenMission }> {
    const resp = await apiClient.post('/api/raven/missions', { query, priority });
    return resp.data;
  },

  // Workspace chats: conversations that open as tabs and continue turn by turn.
  async listWorkspaceChats(workspaceId: string): Promise<WorkspaceChatSummary[]> {
    const resp = await apiClient.get(`/api/workspaces/${workspaceId}/chats`);
    return resp.data?.chats ?? [];
  },

  async createWorkspaceChat(workspaceId: string, title?: string): Promise<WorkspaceChatSummary> {
    const resp = await apiClient.post(`/api/workspaces/${workspaceId}/chats`, { title });
    return resp.data;
  },

  async getWorkspaceChat(workspaceId: string, chatId: string): Promise<WorkspaceChat> {
    const resp = await apiClient.get(`/api/workspaces/${workspaceId}/chats/${chatId}`);
    return resp.data;
  },

  async deleteWorkspaceChat(workspaceId: string, chatId: string): Promise<void> {
    await apiClient.delete(`/api/workspaces/${workspaceId}/chats/${chatId}`);
  },

  /**
   * Run one chat turn and hand each streamed event to `onEvent` as it arrives.
   * Aborting `signal` stops the turn on the server too (it sees the stream close).
   */
  async streamWorkspaceChatTurn(
    workspaceId: string,
    chatId: string,
    body: { message: string; mode: WorkspaceAskMode },
    onEvent: (event: WorkspaceChatEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    await postNdjson(`/api/workspaces/${workspaceId}/chats/${chatId}/turn`, body, onEvent, signal, 'Chat turn failed');
  },

  /** A study answer as it is written: start, text pieces, then done or error. */
  async streamBibleStudy(
    ref: string,
    question: string,
    opts: { version?: string; edition?: string; crossVersion?: boolean; depth?: BibleStudyDepth },
    onEvent: (event: BibleStudyStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    await postNdjson(
      '/api/bible/study/ask/stream',
      {
        ref,
        question,
        version: opts.version,
        edition: opts.edition,
        cross_version: opts.crossVersion ?? false,
        depth: opts.depth ?? 'adult',
      },
      onEvent,
      signal,
      'Study help unavailable',
    );
  },

  async killRavenMission(id: number): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/raven/missions/${id}/kill`);
    return resp.data;
  },

  async deleteRavenMission(id: number): Promise<void> {
    await apiClient.delete(`/api/raven/missions/${id}`);
  },

  async pauseRavenMission(id: number): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/raven/missions/${id}/pause`);
    return resp.data;
  },

  async resumeRavenMission(id: number): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/raven/missions/${id}/resume`);
    return resp.data;
  },

  async refineRavenMission(id: number, prompt: string): Promise<{ status: string; message: string; mission_id: number }> {
    const resp = await apiClient.post(`/api/raven/missions/${id}/refine`, { prompt });
    return resp.data;
  },

  async getMissionLogs(id: number | string): Promise<{ logs: string[] }> {
    const resp = await apiClient.get(`/api/raven/missions/${id}/logs`);
    return resp.data;
  },

  // Identity returns these as plain lists of stored records (group_id /
  // group_name, cluster_id / cluster_name, pattern_id / pattern_name); reading
  // `.groups` / `.clusters` / `.patterns` off them always gave [], so nothing
  // created on the Groups page ever showed up.
  async getMediaGroups(): Promise<MediaGroup[]> {
    const resp = await apiClient.get('/api/groups/media');
    return listOf(resp.data, 'groups').map((g) => ({
      ...g,
      name: String(g.name ?? g.group_id ?? g.group_name ?? ''),
      member_entity_ids: (g.member_entity_ids as string[] | undefined) ?? [],
    })) as MediaGroup[];
  },

  async createMediaGroup(data: { name: string; member_entity_ids: string[]; sync_state?: boolean }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/groups/media', data);
    return resp.data;
  },

  async deleteMediaGroup(name: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/groups/media/${encodeURIComponent(name)}`);
    return resp.data;
  },

  async getLightClusters(): Promise<LightCluster[]> {
    const resp = await apiClient.get('/api/groups/lights');
    return listOf(resp.data, 'clusters').map((c) => ({
      name: String(c.name ?? c.cluster_id ?? c.cluster_name ?? ''),
      member_entity_ids: (c.member_entity_ids as string[] | undefined) ?? [],
    }));
  },

  async createLightCluster(data: { name: string; member_entity_ids: string[]; default_brightness?: number; default_color_temp?: number }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/groups/lights', data);
    return resp.data;
  },

  async deleteLightCluster(name: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/groups/lights/${encodeURIComponent(name)}`);
    return resp.data;
  },

  async getLightPatterns(): Promise<LightPattern[]> {
    const resp = await apiClient.get('/api/groups/patterns');
    return listOf(resp.data, 'patterns').map((p) => ({
      name: String(p.name ?? p.pattern_id ?? p.pattern_name ?? ''),
      steps: (p.steps as unknown[] | undefined) ?? [],
    }));
  },

  async createLightPattern(data: { name: string; steps: Array<{ brightness?: number; color_temp?: number; rgb_color?: number[]; transition?: number; delay?: number }> }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/groups/patterns', data);
    return resp.data;
  },

  async deleteLightPattern(name: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/groups/patterns/${encodeURIComponent(name)}`);
    return resp.data;
  },

  async executeLightPattern(data: { pattern_name: string; target_cluster?: string }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/execute/groups/patterns', {
      action: 'apply',
      pattern_id: data.pattern_name,
      cluster_id: data.target_cluster || null,
    });
    if (resp.data?.status !== 'SUCCESS') throw new Error(resp.data?.message || 'The pattern could not be applied');
    return resp.data;
  },

  async getTelemetryEnrollments(): Promise<TelemetryEnrollment[]> {
    const resp = await apiClient.get('/api/telemetry/enroll');
    return resp.data.enrollments || [];
  },

  async analyzeTelemetry(entityId: string, hours = 168): Promise<{ status: string; message: string }> {
    // entity_id is required by the analysis endpoint; without it the call 400s.
    const resp = await apiClient.post('/api/telemetry/analyze', { entity_id: entityId, hours });
    return resp.data;
  },

  async getIntercomSessions(): Promise<IntercomSessionData[]> {
    const resp = await apiClient.get('/api/intercom/sessions');
    return resp.data || [];
  },

  async startIntercomSession(data: { target_user_id?: string; target_room?: string; target_entity_ids?: string[]; session_type?: string }): Promise<{ session_id: string; status: string }> {
    const resp = await apiClient.post('/api/intercom/sessions', {
      caller_user_id: 'admin',
      ...data,
    });
    return resp.data;
  },

  async endIntercomSession(session_id: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/intercom/sessions/${encodeURIComponent(session_id)}`);
    return resp.data;
  },

  async intercomBroadcast(data: { message: string; target_entity_ids: string[] }): Promise<{ status: string; targets_count: number }> {
    const resp = await apiClient.post('/api/intercom/broadcast', data);
    return resp.data;
  },

  async intercomAnnounce(data: { message: string; target_devices: string[] }): Promise<{ status: string; targets_count: number }> {
    const resp = await apiClient.post('/api/intercom/announce', data);
    return resp.data;
  },

  async getIntercomConfig(): Promise<IntercomConfigData> {
    const resp = await apiClient.get('/api/intercom/config');
    return resp.data;
  },

  async mediaPlay(payload: {
    entity_id?: string;
    device_name?: string;
    query?: string;
    media_type?: string;
    media_content_id?: string;
    enqueue?: string;
    volume?: number;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/play', payload);
    return resp.data;
  },

  async mediaTransport(payload: {
    entity_id?: string;
    command: string;
    volume_level?: number;
    /** Seek target in seconds; only meaningful for command: 'seek'. */
    position?: number;
    /** Target mute state; only meaningful for command: 'volume_mute'. */
    muted?: boolean;
    /** Target shuffle state; only meaningful for command: 'shuffle_set'. */
    shuffle?: boolean;
    /** Target repeat mode; only meaningful for command: 'repeat_set'. */
    repeat?: 'off' | 'all' | 'one';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/transport', payload);
    return resp.data;
  },

  /**
   * Run a Music Assistant JSON-RPC command for a remote speaker.
   *
   * Remote `ma:<id>` players go through the gateway so the browser's Sendspin
   * player stays stopped; only "This device" playback uses it.
   */
  async mediaMaCommand(payload: {
    command: string;
    args?: Record<string, unknown>;
  }): Promise<{ status: string; result?: unknown; message?: string }> {
    const resp = await apiClient.post('/api/media/ma-command', payload);
    return resp.data;
  },

  /** Every media player's state. `silent` for background polling (UX-45). */
  async mediaStatus(opts: { silent?: boolean } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/status', {}, { silent: opts.silent });
    return resp.data;
  },

  async syncMediaState(payload: {
    entity_id: string;
    state: string;
    media_type?: string;
    query?: string;
    media_content_id?: string;
    position?: number;
    duration?: number;
    volume_level?: number;
    is_volume_muted?: boolean;
    media_title?: string;
    media_artist?: string;
    media_album?: string;
    queue?: unknown[];
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/state/sync', payload);
    return resp.data;
  },

  async getMediaDetail(uri: string): Promise<Record<string, unknown>> {
    const resp = await apiClient.get('/api/media/detail', { params: { uri } });
    return resp.data;
  },

  /**
   * Resolve an Audiobookshelf item id to the URI Music Assistant can play.
   *
   * MA is not a URL player: it only accepts its own `library://audiobook/<n>`
   * URI, where `<n>` is an MA-internal id. Sending an `audiobookshelf://` or a
   * stream URL instead leaves the player idle with the URI as its track title.
   * The gateway owns that mapping, so the browser never guesses one.
   *
   * Rejects when MA does not have the book; callers must surface that rather
   * than falling back to an unplayable URI.
   */
  async resolveMALibraryUri(absItemId: string, title = ''): Promise<{ ma_uri: string; title: string }> {
    const resp = await apiClient.post('/api/media/ma-library-uri', {
      abs_item_id: absItemId,
      title,
    });
    return resp.data;
  },

  async setMediaFavorite(uri: string, favorite: boolean): Promise<{ status: string; favorite: boolean }> {
    const resp = await apiClient.post('/api/media/favorite', { uri, favorite });
    return resp.data;
  },

  /** A podcast's episodes, newest first, each with the Music Assistant URI that plays it. */
  async getPodcastEpisodes(absItemId: string, title = ''): Promise<{
    status: string;
    episodes: Array<{ uri: string; name: string; duration: number | null; published: number | null; fully_played: boolean | null; resume_position_ms: number | null }>;
  }> {
    const resp = await apiClient.get('/api/media/podcast-episodes', { params: { abs_item_id: absItemId, title }, timeout: 20000 });
    return resp.data;
  },

  async getAudiobookshelfLastPlayed(): Promise<{ status: string; notice?: string; books: Array<{ id: string; title: string; author: string; progress: number; last_played: string | number; library_id: string; is_complete?: boolean }> }> {
    const resp = await apiClient.get('/api/media/audiobookshelf/last-played', { timeout: 10000 });
    return resp.data;
  },

  async playAudiobook(payload: {
    book_id: string;
    entity_id?: string;
    device_name?: string;
    resume?: boolean;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/audiobookshelf', {
      action: 'play',
      book_id: payload.book_id,
      entity_id: payload.entity_id,
      device_name: payload.device_name,
      resume: payload.resume ?? true,
    });
    return resp.data;
  },

  async getWidgetSettings(): Promise<{
    widgets: Array<{
      widget_key: string;
      visibility: WidgetVisibility;
      order_index: number;
      size: WidgetSize;
      is_pinned: boolean;
      sort_mode: DeviceSortMode | null;
      pinned_devices: string[];
      config: Record<string, unknown>;
      updated_at: number;
    }>;
    quick_assistant_enabled: boolean;
  }> {
    const resp = await apiClient.get('/api/widgets/settings');
    return resp.data;
  },

  // Scheduled telemetry reports (health/fitness + power)

  async getTelemetrySchedules(): Promise<{ jobs: TelemetrySchedule[] }> {
    const resp = await apiClient.get('/api/telemetry/schedules');
    return resp.data;
  },

  async saveTelemetrySchedule(body: {
    type: TelemetryReportType;
    period: TelemetryReportPeriod;
    run_at: string;
    timezone: string;
    enabled: boolean;
  }): Promise<{ job: TelemetrySchedule }> {
    const resp = await apiClient.put('/api/telemetry/schedules', body);
    return resp.data;
  },

  async deleteTelemetrySchedule(jobId: string): Promise<{ deleted: string }> {
    const resp = await apiClient.delete(`/api/telemetry/schedules/${encodeURIComponent(jobId)}`);
    return resp.data;
  },

  async requestTelemetryReport(body: {
    type: TelemetryReportType;
    period: TelemetryReportPeriod;
    timezone?: string;
  }): Promise<{ status: string; job_id: string }> {
    const resp = await apiClient.post('/api/telemetry/reports/request', body);
    return resp.data;
  },

  async getTelemetryReports(params: {
    type?: TelemetryReportType;
    period?: TelemetryReportPeriod;
    limit?: number;
  } = {}): Promise<{ reports: TelemetryReport[] }> {
    const query = new URLSearchParams();
    if (params.type) query.set('type', params.type);
    if (params.period) query.set('period', params.period);
    if (params.limit) query.set('limit', String(params.limit));
    const suffix = query.toString() ? `?${query.toString()}` : '';
    const resp = await apiClient.get(`/api/telemetry/reports${suffix}`);
    return resp.data;
  },

  async getLatestTelemetryReport(
    type: TelemetryReportType = 'health',
    period: TelemetryReportPeriod | 'any' = 'any'
  ): Promise<{ report: TelemetryReport | null }> {
    const query = new URLSearchParams({ type, period });
    const resp = await apiClient.get(`/api/telemetry/reports/latest?${query.toString()}`);
    return resp.data;
  },

  async getTelemetryNotifications(limit = 20): Promise<{ notifications: TelemetryNotification[] }> {
    const resp = await apiClient.get(`/api/telemetry/notifications?limit=${limit}`);
    return resp.data;
  },

  async getPushPublicKey(): Promise<{ public_key: string | null }> {
    const resp = await apiClient.get('/api/telemetry/push/key');
    return resp.data;
  },

  async subscribePush(subscription: unknown): Promise<{ status: string }> {
    const resp = await apiClient.post('/api/telemetry/push/subscribe', { subscription });
    return resp.data;
  },

  async unsubscribePush(endpoint: string): Promise<{ status: string; removed: boolean }> {
    const resp = await apiClient.post('/api/telemetry/push/unsubscribe', { endpoint });
    return resp.data;
  },

  async updateWidgetSettings(widgetKey: string, updates: Partial<{
    visibility: WidgetVisibility;
    order_index: number;
    size: WidgetSize;
    is_pinned: boolean;
    sort_mode: DeviceSortMode | null;
    pinned_devices: string[];
    config: Record<string, unknown>;
    quick_assistant_enabled: boolean;
  }>): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.put(`/api/widgets/settings/${encodeURIComponent(widgetKey)}`, updates);
    return resp.data;
  },

  /**
   * @param date  `today`, or an explicit YYYY-MM-DD.
   * @param scope Whose chores to fetch. Omitted keeps the server default: an
   *   admin sees the whole family frame, anyone else sees only their own. `me`
   *   narrows even an admin to their own login name.
   */
  async getSkylightChores(date?: string, scope?: string): Promise<{
    status: string;
    message?: string;
    chores?: Array<{
      id: string;
      title: string;
      completed: boolean;
      reward?: number;
      assignees?: string[];
      recurrence?: string;
      stars?: number;
      start?: string;
      start_time?: string | null;
      emoji_icon?: string | null;
    }>;
    assignee_meta?: Record<string, string>;
  }> {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    if (scope) params.set('scope', scope);
    const query = params.toString();
    const resp = await apiClient.get(`/api/integrations/skylight/chores${query ? `?${query}` : ''}`);
    return resp.data;
  },

  async completeSkylightChore(choreId: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post(`/api/integrations/skylight/chores/${encodeURIComponent(choreId)}/complete`);
    return resp.data;
  },

  async uncompleteSkylightChore(choreId: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post(`/api/integrations/skylight/chores/${encodeURIComponent(choreId)}/uncomplete`);
    return resp.data;
  },

  async getSkylightRewards(): Promise<{
    status: string;
    message?: string;
    rewards?: Array<{
      id: string;
      name: string;
      star_cost: number;
      icon?: string;
      parent_approval?: boolean;
    }>;
  }> {
    const resp = await apiClient.get('/api/integrations/skylight/rewards');
    return resp.data;
  },

  async redeemSkylightReward(rewardId: string, username?: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post(`/api/integrations/skylight/rewards/${encodeURIComponent(rewardId)}/redeem`, {
      user_id: username,
    });
    return resp.data;
  },

  async getDeviceStates(domains?: string[]): Promise<DeviceEntry[]> {
    const resp = await apiClient.post('/execute/entity/search', {
      query: '',
      domain: domains && domains.length > 0 ? domains.join(',') : null,
      area: null,
      state: null,
      limit: 500,
    });
    return (resp.data.result || []).map((e: { entity_id: string; friendly_name: string; state: string; domain: string; area_id?: string; attributes?: Record<string, number | string | boolean | string[] | null>; last_changed?: string; last_updated?: string }) => {
      const ts = e.last_updated || e.last_changed;
      let lastActivated: number | undefined;
      if (ts) {
        const parsed = Date.parse(ts);
        if (!Number.isNaN(parsed)) lastActivated = parsed;
      }
      return {
        entity_id: e.entity_id,
        friendly_name: e.friendly_name,
        state: e.state,
        domain: e.domain,
        room: e.area_id || undefined,
        last_activated: lastActivated,
        attributes: e.attributes || undefined,
      };
    });
  },

  async toggleDevice(entityId: string, action: 'on' | 'off'): Promise<{ status: string; message?: string }> {
    const domain = entityId.split('.')[0];
    const resp = await apiClient.post('/execute/ha_service', {
      domain,
      service: action === 'on' ? 'turn_on' : 'turn_off',
      entity_id: entityId,
      service_data: null,
    });
    return resp.data;
  },

  /**
   * Call any Home Assistant service. `serviceData` maps to the service's
   * own fields (e.g. { temperature: 72 } for climate.set_temperature).
   */
  async callHaService(
    domain: string,
    service: string,
    entityId: string,
    serviceData?: Record<string, unknown> | null
  ): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post('/execute/ha_service', {
      domain,
      service,
      entity_id: entityId,
      service_data: serviceData ?? null,
    });
    return resp.data;
  },

  // Companion devices: the signed-in user's own (phones register themselves
  // on login; watches and assistants are added with pairDevice).
  async getCompanionDevices(): Promise<CompanionDevice[]> {
    const resp = await apiClient.get('/api/user-panel/devices');
    return resp.data;
  },

  // A device's battery reports, oldest first (its owner's, or any for an admin).
  async getDeviceBattery(deviceKey: string, hours = 24): Promise<BatteryReading[]> {
    const resp = await apiClient.get(`/api/user-panel/devices/${encodeURIComponent(deviceKey)}/battery`, {
      params: { hours },
    });
    return resp.data;
  },

  // What one device has reported lately: its record, how many of each event
  // arrived and the most recent ones. Every event is from the no-opt-in
  // allowlist, so it is activity and small scalars, never content.
  async getDeviceActivity(deviceKey: string, hours = 24, limit = 50): Promise<DeviceActivity> {
    const resp = await apiClient.get(
      `/api/user-panel/devices/${encodeURIComponent(deviceKey)}/activity`,
      { params: { hours, limit } },
    );
    return resp.data;
  },

  // Per-device step history: {source: {day: steps}}. A source with nothing
  // recorded is absent rather than zero, so a device page can tell "never
  // reported" from "reported nothing". The owner is named rather than assumed,
  // because a device page may be looking at someone else's device; the server
  // applies the usual consent rule to whoever is asking.
  async getStepSources(days = 7, hourly = false, userId?: string): Promise<StepSources> {
    const resp = await apiClient.get('/api/geo/steps/sources', {
      params: { days, hourly: hourly ? 'true' : undefined, user_id: userId },
    });
    return resp.data;
  },

  // Admin: give a companion device to a user ('' unassigns it).
  async assignCompanionDevice(deviceKey: string, ownerUsername: string): Promise<CompanionDevice> {
    const resp = await apiClient.patch(`/api/user-panel/devices/${encodeURIComponent(deviceKey)}`, {
      owner_username: ownerUsername,
    });
    return resp.data;
  },

  async pairDevice(body: PairDeviceRequest): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/devices/pair', body, { timeout: 60000 });
    return resp.data;
  },

  // Direct ESPHome native-API control (bypasses Home Assistant)
  async esphomeList(device: string): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/esphome', {
      action: 'list',
      device,
    });
    return resp.data;
  },

  async esphomeCommand(device: string, entity: string, params?: Record<string, unknown>): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/esphome', {
      action: 'call',
      device,
      entity,
      params: params ?? null,
    });
    return resp.data;
  },

  async getEsphomeDevices(): Promise<EsphomeDevice[]> {
    const settings = await api.getSettings();
    const raw = settings.find(s => s.key === 'esphome_devices')?.value ?? '[]';
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  },

  async saveEsphomeDevices(devices: EsphomeDevice[]): Promise<GlobalSetting> {
    return api.updateSetting('esphome_devices', JSON.stringify(devices));
  },

  async getSystemHealth(): Promise<SystemHealthStatus> {
    const resp = await apiClient.get('/api/admin/services/health');
    return resp.data;
  },

  // Telemetry
  async getTelemetryData(entityId: string, hours?: number): Promise<TelemetryDataResponse> {
    const params = hours ? { hours } : {};
    const resp = await apiClient.get(`/api/telemetry/data/${entityId}`, { params });
    return resp.data;
  },

  async getTelemetrySummary(entityId: string): Promise<TelemetrySummary> {
    const resp = await apiClient.get(`/api/telemetry/summary/${entityId}`);
    return resp.data;
  },

  async triggerTelemetrySnapshot(entityId: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/telemetry/snapshot/${entityId}`);
    return resp.data;
  },

  async getTelemetryInsights(entityId: string): Promise<TelemetryInsights> {
    const resp = await apiClient.get(`/api/telemetry/insights/${entityId}`);
    return resp.data;
  },

  async enrollTelemetry(entityId: string, config: Partial<TelemetryEnrollment>): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/telemetry/enroll/${entityId}`, config);
    return resp.data;
  },

  async updateTelemetryEnrollment(entityId: string, config: Partial<TelemetryEnrollment>): Promise<{ status: string; message: string }> {
    const resp = await apiClient.put(`/api/telemetry/enroll/${entityId}`, config);
    return resp.data;
  },

  async unenrollTelemetry(entityId: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/telemetry/enroll/${entityId}`);
    return resp.data;
  },

  // Models
  async getModels(): Promise<ModelsResponse> {
    const resp = await apiClient.get('/api/models');
    return resp.data;
  },

  async switchModel(modelName: string): Promise<ModelSwitchResponse> {
    const resp = await apiClient.post('/api/models/switch', { model_name: modelName });
    return resp.data;
  },

  async unloadModel(): Promise<ModelSwitchResponse> {
    const resp = await apiClient.post('/api/models/unload');
    return resp.data;
  },

  // Ollama-compatible endpoints
  async generate(request: GenerateRequest): Promise<GenerateResponse> {
    const resp = await apiClient.post('/api/generate', request);
    return resp.data;
  },

  async embed(request: EmbeddingsRequest): Promise<EmbeddingsResponse> {
    const resp = await apiClient.post('/api/embeddings', request);
    return resp.data;
  },

  async getTags(): Promise<TagsResponse> {
    const resp = await apiClient.get('/api/tags');
    return resp.data;
  },

  async showModel(request: ShowRequest): Promise<ModelInfo> {
    const resp = await apiClient.post('/api/show', request);
    return resp.data;
  },

  async getHistory(): Promise<Record<string, unknown>[]> {
    const resp = await apiClient.get('/api/history');
    return resp.data;
  },

  async deleteHistory(): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete('/api/history');
    return resp.data;
  },

  // Workspace files (POST-backed; backend routes are POST)
  async listWorkspaceFiles(workspaceId: string, relative_path = '.', recursive = false, max_depth = 3): Promise<WorkspaceFileListResponse> {
    const resp = await apiClient.post('/api/workspaces/files/list', { workspace_id: workspaceId, relative_path, recursive, max_depth });
    return resp.data;
  },

  async readWorkspaceFile(workspaceId: string, relative_path: string): Promise<WorkspaceFileReadResponse> {
    const resp = await apiClient.post('/api/workspaces/files/read', { workspace_id: workspaceId, relative_path });
    return resp.data;
  },

  async writeWorkspaceFile(workspaceId: string, relative_path: string, content: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post('/api/workspaces/files/write', { workspace_id: workspaceId, relative_path, content });
    return resp.data;
  },

  async deleteWorkspaceFile(workspaceId: string, relative_path: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post('/api/workspaces/files/delete', { workspace_id: workspaceId, relative_path });
    return resp.data;
  },

  async moveWorkspaceFile(workspaceId: string, relative_path: string, new_relative_path: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post('/api/workspaces/files/move', { workspace_id: workspaceId, relative_path, new_relative_path });
    return resp.data;
  },

  // Raw binary (e.g. images) served as a blob for in-browser preview.
  async fetchWorkspaceFileRaw(workspaceId: string, relative_path: string): Promise<Blob> {
    const resp = await apiClient.post('/api/workspaces/files/raw', { workspace_id: workspaceId, relative_path }, { responseType: 'blob' });
    return resp.data;
  },

  // Zip multiple workspace files (possibly across workspaces) into one archive.
  async zipWorkspaceFiles(entries: { workspace_id: string; relative_path: string }[]): Promise<Blob> {
    const resp = await apiClient.post('/api/workspaces/files/zip', { entries }, { responseType: 'blob' });
    return resp.data;
  },

  async writeWorkspaceFileBase64(workspaceId: string, relative_path: string, content_base64: string): Promise<{ status: string; message?: string }> {
    const resp = await apiClient.post('/api/workspaces/files/write', { workspace_id: workspaceId, relative_path, content_base64 });
    return resp.data;
  },

  // Stable Diffusion image tasks (proxied to the alpaca SD backend).
  async generateImage(payload: { prompt: string; model?: string; size?: string; n?: number }): Promise<{ status: string; data?: Array<{ url?: string; b64_json?: string }>; message?: string }> {
    const resp = await apiClient.post('/api/images/generate', payload);
    return resp.data;
  },

  // Edit a picture sent as a data: URL (Family drawings). The gateway starts
  // a job and this polls it, so a long edit never depends on one HTTP request
  // staying open across every proxy. Older gateways answer 404 for the job
  // route, in which case the original synchronous call is still available.
  async editImage(
    payload: { prompt: string; image: string; model?: string; purpose?: string },
    options: { signal?: AbortSignal } = {},
  ): Promise<ImageEditResult> {
    let started: ImageEditResult;
    try {
      const resp = await apiClient.post(
        '/api/images/edit/jobs',
        { ...payload, request_id: newImageEditRequestId() },
        { timeout: 30_000, signal: options.signal },
      );
      started = resp.data;
    } catch (error) {
      if (!isNotFound(error)) throw error;
      const resp = await apiClient.post('/api/images/edit', payload, {
        timeout: 1_200_000,
        signal: options.signal,
      });
      return resp.data;
    }
    if (!['PENDING', 'RUNNING'].includes(started.status) || !started.job_id) return started;
    return waitForImageEdit(fetchImageEditJob, started.job_id, options);
  },

  // Workspace-scoped AI image edit (execution service path). Uses a dedicated
  // long-timeout request: CPU-offloaded image editing takes minutes, and the
  // shared apiClient aborts at 15s.
  //
  // A face swap is a two-image edit: `image_path` is the photo to keep and
  // `face_image_path` is the donor face copied onto it. The server rejects a
  // swap prompt with no donor, so both images are required for a swap.
  async workspaceEditImage(workspaceId: string, payload: { prompt: string; image_path: string; face_image_path?: string; output_path?: string; model?: string; size?: string }): Promise<{ status: string; message?: string; detail?: { output_path?: string; face_swapped?: boolean; face_image_path?: string | null } }> {
    const { baseURL, headers } = directRequestConfig();
    const resp = await axios.post(`/api/workspaces/${workspaceId}/images/edit`, payload, {
      baseURL,
      headers,
      timeout: 640000,
    });
    return resp.data;
  },

  async listImageModels(): Promise<{ status: string; models?: string[]; message?: string }> {
    const resp = await apiClient.get('/api/images/models');
    return resp.data;
  },

  /**
   * What the AI tools can actually do right now. `available: null` means the
   * backend cannot be judged without trying a real request, so the UI must show
   * that as unconfirmed rather than rounding it to yes or no.
   */
  async getAiCapabilities(): Promise<{ capabilities: AiCapability[] }> {
    const resp = await apiClient.get('/api/ai/capabilities');
    return resp.data;
  },

  // Workspace-scoped OCR via the execution service (same path Raven uses).
  // Long timeout: the vision LLM runs on CPU and can take a minute or two.
  async workspaceOcr(workspaceId: string, payload: { image_path: string; task?: string; model?: string }): Promise<{
    status: string;
    message?: string;
    detail?: { full_text?: string; headline?: string; subtext?: string; badge?: string };
  }> {
    const { baseURL, headers } = directRequestConfig();
    const resp = await axios.post(`/api/workspaces/${workspaceId}/ocr`, payload, {
      baseURL,
      headers,
      timeout: 640000,
    });
    return resp.data;
  },

  // Workspace git (tool panel)
  async workspaceGitStatus(workspaceId: string): Promise<GitStatusResponse> {
    const resp = await apiClient.post('/api/workspaces/git/status', { workspace_id: workspaceId });
    return resp.data;
  },
  async workspaceGitBranches(workspaceId: string): Promise<GitBranchesResponse> {
    const resp = await apiClient.post('/api/workspaces/git/branches', { workspace_id: workspaceId });
    return resp.data;
  },
  async workspaceGitCheckout(workspaceId: string, branch: string, create = false, fromRef?: string): Promise<GitCheckoutResponse> {
    const resp = await apiClient.post('/api/workspaces/git/checkout', { workspace_id: workspaceId, branch, create, from_ref: fromRef });
    return resp.data;
  },
  async workspaceGitDiff(workspaceId: string, ref = 'HEAD'): Promise<GitDiffResponse> {
    const resp = await apiClient.post('/api/workspaces/git/diff', { workspace_id: workspaceId, ref });
    return resp.data;
  },
  async workspaceGitAdd(workspaceId: string, pathspecs: string[] = []): Promise<GitStatusResponse> {
    const resp = await apiClient.post('/api/workspaces/git/add', { workspace_id: workspaceId, pathspecs });
    return resp.data;
  },
  async workspaceGitCommit(workspaceId: string, message: string, pathspecs: string[] = []): Promise<GitCommitResponse> {
    const resp = await apiClient.post('/api/workspaces/git/commit', { workspace_id: workspaceId, message, pathspecs });
    return resp.data;
  },
  async workspaceGitPush(workspaceId: string, remote?: string, branch?: string): Promise<GitPushResponse> {
    const resp = await apiClient.post('/api/workspaces/git/push', { workspace_id: workspaceId, remote, branch });
    return resp.data;
  },
  async workspaceGitLog(workspaceId: string, max_count = 20): Promise<GitLogResponse> {
    const resp = await apiClient.post('/api/workspaces/git/log', { workspace_id: workspaceId, max_count });
    return resp.data;
  },
  async workspaceGitFetch(workspaceId: string): Promise<GitPushResponse> {
    const resp = await apiClient.post('/api/workspaces/git/fetch', { workspace_id: workspaceId });
    return resp.data;
  },

  // Two-way (or one-way) sync between a workspace and its Nextcloud folder.
  // Omit direction for the workspace default: two-way for Nextcloud-backed
  // workspaces, push (backup) for git-only ones. A first sync of a large
  // folder can take minutes, so no client timeout.
  async syncWorkspace(workspaceId: string, opts: { direction?: WorkspaceSyncDirection; dry_run?: boolean } = {}): Promise<WorkspaceSyncResponse> {
    const resp = await apiClient.post('/api/workspaces/sync', { workspace_id: workspaceId, ...opts }, { timeout: 0 });
    return resp.data;
  },

  // Upload many files and/or folder trees in one multipart request. `paths`
  // are relative to `relative_path`; `dirs` are folders to create even when
  // empty. Callers batch large selections (see lib/workspaceUpload.ts).
  async uploadWorkspaceFiles(
    workspaceId: string,
    relative_path: string,
    items: { file: File; path: string }[],
    dirs: string[] = [],
    opts: { overwrite?: boolean; onUploadBytes?: (sent: number) => void } = {},
  ): Promise<WorkspaceUploadResponse> {
    const form = new FormData();
    form.append('workspace_id', workspaceId);
    form.append('relative_path', relative_path);
    form.append('overwrite', opts.overwrite === false ? 'false' : 'true');
    for (const d of dirs) form.append('dirs', d);
    for (const item of items) {
      form.append('files', item.file, item.file.name);
      form.append('paths', item.path);
    }
    const resp = await apiClient.post('/api/workspaces/files/upload', form, {
      // The instance default is JSON, which would make axios serialise the
      // FormData as JSON; multipart lets the browser add the boundary.
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      onUploadProgress: (e) => opts.onUploadBytes?.(e.loaded),
    });
    return resp.data;
  },

  // Missions for a workspace (edit-last-mission / inspect)
  async getWorkspaceMissions(workspaceId: string, limit = 50): Promise<RavenMission[]> {
    const resp = await apiClient.get(`/api/workspaces/${encodeURIComponent(workspaceId)}/raven/missions?limit=${limit}`);
    return resp.data;
  },

  // Admin volumes & service logs
  async getVolumes(): Promise<VolumesResponse> {
    const resp = await apiClient.get('/api/admin/volumes');
    return resp.data;
  },

  async getServiceLogs(serviceName: string, lines?: number): Promise<ServiceLogsResponse> {
    const params = lines ? { lines } : {};
    const resp = await apiClient.get(`/api/admin/services/${serviceName}/logs`, { params });
    return resp.data;
  },

  // Control plane exec
  async execContainer(serviceName: string, cmd: string[], env?: Record<string, string>): Promise<ContainerExecResponse> {
    const resp = await apiClient.post(`/api/containers/${serviceName}/exec`, { cmd, env });
    return resp.data;
  },

  // RAG
  async getRagIndexedPaths(): Promise<RagIndexedPathsResponse> {
    const resp = await apiClient.get('/rag/indexed-paths');
    return resp.data;
  },

  async syncRagFiles(request: RagSyncFilesRequest): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/rag/sync/files', request);
    return resp.data;
  },

  // Execution service endpoints
  async workspaceLint(request: WorkspaceLintRequest): Promise<WorkspaceLintResponse> {
    const resp = await apiClient.post('/execute/workspace_lint', request);
    return resp.data;
  },

  async executeVolumes(request: VolumesExecuteRequest): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/execute/volumes', request);
    return resp.data;
  },

  // Discovery
  async getDiscoveryProfile(entityId: string): Promise<DiscoveryProfileResponse> {
    const resp = await apiClient.get(`/discovery/profile/${entityId}`);
    return resp.data;
  },

  async networkScan(request: NetworkScanRequest): Promise<NetworkScanResponse> {
    const resp = await apiClient.post('/discovery/network_scan', request);
    return resp.data;
  },

  async pullServiceImage(serviceName: string): Promise<ImagePullResult> {
    const resp = await apiClient.post(`/api/admin/services/${serviceName}/pull`);
    return resp.data;
  },

  async getPullStatus(serviceName: string): Promise<PullStatus> {
    const resp = await apiClient.get(`/api/admin/services/${serviceName}/pull/status`);
    return resp.data;
  },

  async pullAndRestart(serviceName: string): Promise<PullAndRestartResult> {
    // Pull + recreate can exceed the default 15s axios timeout (gateway allows 120s).
    const resp = await apiClient.post(`/api/admin/services/${serviceName}/pull-and-restart`, undefined, {
      timeout: 130000,
    });
    return resp.data;
  },

  async checkAllUpdates(): Promise<CheckUpdatesResponse> {
    // Registry digest checks can be slow (gateway allows 60s).
    const resp = await apiClient.get('/api/admin/services/updates', { timeout: 90000 });
    return resp.data;
  },

  async restartService(serviceName: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/admin/services/${serviceName}/restart`);
    return resp.data;
  },

  // Bible (reading app)
  //
  // No method here takes a username. The gateway always scopes reading state to
  // the caller, so a client that could name someone else would either leak
  // their position or be silently ignored -- neither is worth the API surface.
  // The one cross-user read, `getBibleActivitySummary`, is consent-gated
  // server-side and exists so the family view can ask.
  async getBibleDaily(work?: string): Promise<BibleDailyResponse> {
    const resp = await apiClient.get('/api/bible/daily', { params: work ? { work } : undefined });
    return resp.data;
  },

  async getBibleVersions(): Promise<{ versions: BibleVersionInfo[]; message?: string }> {
    return withOfflineCopy('versions', async () => (await apiClient.get('/api/bible/versions')).data);
  },

  async getBibleBooks(version?: string): Promise<{ version: string; books: BibleBookInfo[] }> {
    return withOfflineCopy(`books:${version ?? ''}`, async () =>
      (await apiClient.get('/api/bible/books', { params: { version } })).data,
    );
  },

  /**
   * A passage, kept on the device as it is read so it still opens offline. Only
   * an unreachable server falls back to the device copy: an answer from the
   * server (a bad reference, a missing translation) is always shown as itself.
   */
  async getBiblePassage(ref: string, version?: string): Promise<OfflinePassage> {
    try {
      const resp = await apiClient.get('/api/bible/passages', { params: { ref, version } });
      rememberPassage(ref, version, resp.data);
      return resp.data;
    } catch (error) {
      const saved = isUnreachable(error) ? recallPassage(ref, version) : null;
      if (saved) return saved;
      throw error;
    }
  },

  async searchBible(q: string, options: { version?: string; book?: string; limit?: number } = {}): Promise<BibleSearchResult> {
    const resp = await apiClient.get('/api/bible/search', { params: { q, ...options } });
    return resp.data;
  },

  async getBibleVerseOfDay(params: { day?: string; version?: string; scope?: 'all' | 'ot' | 'nt' } = {}): Promise<BibleVerseOfDay> {
    const resp = await apiClient.get('/api/bible/verse-of-day', { params });
    return resp.data;
  },

  /**
   * Jarvis's context note for the Verse of the Day. The first request for a
   * verse can take as long as a study answer; later ones are served from the
   * stored note. `refresh` asks for a new note.
   */
  async getBibleVerseContext(
    params: { day?: string; version?: string; scope?: 'all' | 'ot' | 'nt'; refresh?: boolean } = {},
  ): Promise<BibleVerseContext> {
    const resp = await apiClient.post('/api/bible/verse-of-day/context', params, { timeout: 320_000 });
    return resp.data.context;
  },

  async getBibleDevotional(params: { day?: string; work?: string } = {}): Promise<BibleDevotionalResponse> {
    const resp = await apiClient.get('/api/bible/devotional', { params });
    return resp.data;
  },

  async getBibleDevotionalSources(): Promise<{ sources: BibleDevotionalSourceInfo[] }> {
    const resp = await apiClient.get('/api/bible/devotional/sources');
    return resp.data;
  },

  async getBibleMarks(osis?: string): Promise<{ marks: BibleMark[] }> {
    const resp = await apiClient.get('/api/bible/marks', { params: { osis } });
    return resp.data;
  },

  async putBibleMark(payload: {
    ref: string;
    kind?: 'highlight' | 'bookmark' | 'note';
    color?: string;
    version_code?: string;
    note_path?: string;
    note_preview?: string;
  }): Promise<{ mark: BibleMark }> {
    const resp = await apiClient.put('/api/bible/marks', payload);
    return resp.data;
  },

  async deleteBibleMark(markId: number): Promise<{ deleted: number }> {
    const resp = await apiClient.delete(`/api/bible/marks/${markId}`);
    return resp.data;
  },

  async getBibleState(): Promise<BibleStateResponse> {
    return withOfflineCopy('state', async () => (await apiClient.get('/api/bible/state')).data);
  },

  async putBibleState(payload: Partial<BiblePosition> & Partial<BiblePreferences>): Promise<BibleStateResponse> {
    const resp = await apiClient.put('/api/bible/state', payload);
    return resp.data;
  },

  async recordBibleEvent(kind: string, ref = '', value = 0): Promise<{ ok: boolean }> {
    const resp = await apiClient.post('/api/bible/events', { kind, ref, value });
    return resp.data;
  },

  async getBibleStats(days = 30): Promise<BibleStats> {
    const resp = await apiClient.get('/api/bible/stats', { params: { days } });
    return resp.data;
  },

  async getBibleStreaks(): Promise<BibleStreaks> {
    const resp = await apiClient.get('/api/bible/streaks');
    return resp.data;
  },

  async getBibleAchievements(): Promise<BibleAchievementsResponse> {
    const resp = await apiClient.get('/api/bible/achievements');
    return resp.data;
  },

  async getBibleGames(): Promise<{ modes: BibleGameInfo[]; today: string }> {
    const resp = await apiClient.get('/api/bible/games');
    return resp.data;
  },

  async getBibleGame(mode: BibleGameMode, opts: { seed?: string; version?: string } = {}): Promise<BibleGame> {
    const resp = await apiClient.get(`/api/bible/games/${mode}`, { params: opts });
    return resp.data;
  },

  async submitBibleGame(
    mode: BibleGameMode,
    body: { seed: string; version: string; answers: (string | null)[]; duration_ms: number },
  ): Promise<BibleGameResult> {
    const resp = await apiClient.post(`/api/bible/games/${mode}/attempt`, body);
    return resp.data;
  },

  async getBibleGameReview(attemptId: number): Promise<BibleGameReview> {
    const resp = await apiClient.get(`/api/bible/games/attempts/${attemptId}`);
    return resp.data;
  },

  async getBibleLeaderboard(): Promise<BibleLeaderboard> {
    const resp = await apiClient.get('/api/bible/games/leaderboard');
    return resp.data;
  },

  async getBibleMemory(): Promise<BibleMemoryOverview> {
    const resp = await apiClient.get('/api/bible/memorize');
    return resp.data;
  },

  async addBibleMemory(body: { ref?: string; set_id?: string }): Promise<BibleMemoryOverview> {
    const resp = await apiClient.post('/api/bible/memorize', body);
    return resp.data;
  },

  async removeBibleMemory(id: number): Promise<BibleMemoryOverview> {
    const resp = await apiClient.delete(`/api/bible/memorize/${id}`);
    return resp.data;
  },

  async getBibleMemoryDue(limit = 20): Promise<{ due: BibleMemoryVerse[]; count: number }> {
    const resp = await apiClient.get('/api/bible/memorize/due', { params: { limit } });
    return resp.data;
  },

  async recallBibleMemory(id: number, typed: string): Promise<BibleRecallResult> {
    const resp = await apiClient.post(`/api/bible/memorize/${id}/recall`, { typed });
    return resp.data;
  },

  async getBiblePlans(): Promise<{ catalogue: BiblePlanInfo[]; mine: BiblePlanEnrollment[] }> {
    const resp = await apiClient.get('/api/bible/plans');
    return resp.data;
  },

  async getBiblePlan(planId: string): Promise<BiblePlanDetail> {
    const resp = await apiClient.get(`/api/bible/plans/${planId}`);
    return resp.data;
  },

  async getBiblePlanFamily(
    planId: string,
  ): Promise<{ plan_id: string; members: { username: string; completed: number; percent: number; behind: number; finished: boolean }[] }> {
    const resp = await apiClient.get(`/api/bible/plans/${planId}/family`);
    return resp.data;
  },

  async startBiblePlan(planId: string): Promise<BiblePlanEnrollment> {
    const resp = await apiClient.post(`/api/bible/plans/${planId}/start`);
    return resp.data;
  },

  async completeBiblePlanDay(planId: string, day: number): Promise<BiblePlanEnrollment> {
    const resp = await apiClient.post(`/api/bible/plans/${planId}/days/${day}/complete`);
    return resp.data;
  },

  /** What the reader took from a plan day; kept and put into study memory. */
  async saveBiblePlanReflection(planId: string, day: number, text: string): Promise<{ day: number; text: string; remembered?: boolean }> {
    const resp = await apiClient.put(`/api/bible/plans/${planId}/days/${day}/reflection`, { text });
    return resp.data;
  },

  async getBiblePlanReflections(planId: string): Promise<{ reflections: Array<{ day: number; text: string; updated_at: string }> }> {
    const resp = await apiClient.get(`/api/bible/plans/${planId}/reflections`);
    return resp.data;
  },

  /** Promote one of the reader's verse notes into study memory. */
  async rememberBibleNote(markId: number, text: string, reference: string): Promise<{ remembered: boolean; id: string }> {
    const resp = await apiClient.post(`/api/bible/marks/${markId}/remember`, { text, reference });
    return resp.data;
  },

  async undoBiblePlanDay(planId: string, day: number): Promise<BiblePlanEnrollment> {
    const resp = await apiClient.delete(`/api/bible/plans/${planId}/days/${day}`);
    return resp.data;
  },

  async catchUpBiblePlan(planId: string): Promise<BiblePlanEnrollment> {
    const resp = await apiClient.post(`/api/bible/plans/${planId}/catch-up`);
    return resp.data;
  },

  async stopBiblePlan(planId: string): Promise<{ stopped: string }> {
    const resp = await apiClient.delete(`/api/bible/plans/${planId}`);
    return resp.data;
  },

  async getBibleActivitySummary(userId: string): Promise<BibleActivitySummary> {
    const resp = await apiClient.get('/api/bible/activity/summary', { params: { user_id: userId } });
    return resp.data;
  },

  async getBibleActivityFeed(limit = 20): Promise<BibleActivityFeed> {
    const resp = await apiClient.get('/api/bible/activity/feed', { params: { limit } });
    return resp.data;
  },

  /** A Blue Letter Bible link: the verse in `version` (KJV when BLB lacks it), or its interlinear tab. */
  async getBlbLink(ref: string, tool?: 'interlinear', version?: string): Promise<BibleBlbLink> {
    const resp = await apiClient.get('/api/bible/blb/link', { params: { ref, tool, version } });
    return resp.data;
  },

  /** The imported word-by-word interlinear for a passage (`available` false when none). */
  async getBibleInterlinear(ref: string): Promise<BibleInterlinear> {
    const resp = await apiClient.get('/api/bible/interlinear', { params: { ref } });
    return resp.data;
  },

  async getBibleEditions(version?: string): Promise<BibleEditionsResponse> {
    const resp = await apiClient.get('/api/bible/editions', {
      params: { version },
    });
    return resp.data;
  },

  async getBibleStudyNotes(
    ref: string,
    version?: string,
    opts: {
      edition?: string;
      kind?: BibleStudyNoteKind[];
      crossVersion?: boolean;
    } = {},
  ): Promise<BibleStudyNotesResponse> {
    const resp = await apiClient.get('/api/bible/study/notes', {
      params: {
        ref,
        version,
        edition: opts.edition,
        kind: opts.kind?.join(','),
        cross_version: opts.crossVersion ? true : undefined,
      },
    });
    return resp.data;
  },

  async getBibleVoices(): Promise<BibleVoicesResponse> {
    const resp = await apiClient.get('/api/bible/voices');
    return resp.data;
  },
  async getBibleNarration(ref: string, version?: string, voice?: string): Promise<BibleNarration> {
    const resp = await apiClient.get('/api/bible/narration', {
      params: { ref, version, voice },
      timeout: 320_000,
    });
    return resp.data;
  },

  /**
   * How a passage will be spoken: how many pieces, and which are already cached.
   *
   * Costs nothing to ask, and lets playback start on the first piece instead of
   * waiting for a whole chapter to be rendered.
   */
  async getBibleNarrationPlan(
    ref: string,
    version?: string,
    voice?: string,
  ): Promise<BibleNarrationPlan> {
    const resp = await apiClient.get('/api/bible/narration/plan', {
      params: { ref, version, voice },
      timeout: 320_000,
    });
    return resp.data;
  },

  /**
   * One piece of a passage's narration.
   *
   * The generous timeout is the engine's, not the network's: synthesis is
   * CPU-bound and the server may be busy, and a piece that is refused after
   * minutes is worse than one that arrives late.
   */
  async getBibleNarrationChunk(
    ref: string,
    index: number,
    version?: string,
    voice?: string,
  ): Promise<BibleNarrationChunk> {
    const resp = await apiClient.get('/api/bible/narration/chunk', {
      params: { ref, index, version, voice },
      timeout: 320_000,
    });
    return resp.data;
  },

  /**
   * Ask about the passage being read.
   *
   * The bible service picks the verses and study notes itself, so the question
   * cannot be asked about a passage other than the one named here. A cold model
   * load can take minutes, hence the long timeout: the reading routes abort at
   * 15s and this one must not.
   */


  async getBibleImports(): Promise<BibleImportsResponse> {
    const resp = await apiClient.get('/api/bible/admin/imports');
    return resp.data;
  },

  async getBibleProviderTranslations(
    code: string,
  ): Promise<{ provider: BibleImportProviderInfo; translations: BibleRemoteTranslation[]; count: number }> {
    const resp = await apiClient.get(`/api/bible/admin/providers/${code}/translations`);
    return resp.data;
  },

  async runBibleImport(payload: {
    code: string;
    kind: BibleImportKind;
    name?: string;
    sha256?: string;
    source_path?: string;
    edition?: string;
    edition_name?: string;
    publisher?: string;
    rights_holder?: string;
    import_notes?: boolean;
    provider?: string;
    provider_id?: string;
    /** A file in the Nextcloud book library; fetched and then imported. */
    library_path?: string;
    /** Fetch one book to prove the parsing, then install nothing. */
    dry_run?: boolean;
    /** Refuse this run if it would need more than this many new requests. */
    budget?: number;
  }): Promise<BibleImportRun> {
    const resp = await apiClient.post('/api/bible/admin/imports', payload);
    return resp.data;
  },

  async uploadBibleImport(form: FormData): Promise<BibleImportRun> {
    const resp = await apiClient.post('/api/bible/admin/imports/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return resp.data;
  },

  /** Imported devotional and study books (admin). */
  async getBibleAdminBooks(): Promise<BibleImportedBooksResponse> {
    const resp = await apiClient.get('/api/bible/admin/books');
    return resp.data;
  },

  /** Browse Nextcloud Books or the Calibre shelf for PDF/EPUB books. */
  async browseBibleBooks(source: 'nextcloud' | 'calibre', path = ''): Promise<BibleBookListing> {
    const resp = await apiClient.get('/api/bible/admin/books/browse', {
      params: { source, path },
      timeout: 90_000,
    });
    return resp.data;
  },

  async importBibleBook(payload: {
    source: 'nextcloud' | 'calibre';
    path: string;
    title?: string;
    author?: string;
    kind: 'devotional' | 'study';
  }): Promise<{ status: string; book: BibleImportedBookDetail['book']; bytes?: number }> {
    const resp = await apiClient.post('/api/bible/admin/books/import', payload, { timeout: 180_000 });
    return resp.data;
  },

  async uploadBibleBook(form: FormData): Promise<{ status: string; book: BibleImportedBookDetail['book']; bytes?: number }> {
    const resp = await apiClient.post('/api/bible/admin/books/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180_000,
    });
    return resp.data;
  },

  async deleteBibleBook(bookId: number): Promise<{ status: string; id: number }> {
    const resp = await apiClient.delete(`/api/bible/admin/books/${bookId}`);
    return resp.data;
  },

  /** Books available to readers. */
  async getBibleImportedBooks(kind?: 'devotional' | 'study'): Promise<BibleImportedBooksResponse> {
    const resp = await apiClient.get('/api/bible/imported-books', { params: kind ? { kind } : undefined });
    return resp.data;
  },

  async getBibleImportedBook(bookId: number): Promise<BibleImportedBookDetail> {
    const resp = await apiClient.get(`/api/bible/imported-books/${bookId}`);
    return resp.data;
  },

  async getBibleImportedBookChapter(bookId: number, ordinal: number): Promise<BibleBookChapter> {
    const resp = await apiClient.get(`/api/bible/imported-books/${bookId}/chapters/${ordinal}`);
    return resp.data;
  },

  async askBibleBookQuestion(
    bookId: number,
    ordinal: number,
    question: string,
  ): Promise<{ answer: string; book_id: number; ordinal: number; question: string }> {
    const resp = await apiClient.post(
      `/api/bible/imported-books/${bookId}/chapters/${ordinal}/ask`,
      { question },
      { timeout: 300_000 },
    );
    return resp.data;
  },

  async getBibleDevotionalWorks(): Promise<{ works: BibleDevotionalWork[] }> {
    const resp = await apiClient.get('/api/bible/devotional/works');
    return resp.data;
  },

  /** Store how a devotional advances: a reading every day or one a week. */
  async setBibleDevotionalCadence(bookId: number, cadence: 'day' | 'week') {
    const res = await apiClient.post<{ book: BibleImportedBook }>(
      `/api/bible/devotional/book/${bookId}/cadence`,
      { cadence },
      { timeout: 60_000 },
    );
    return res.data;
  },

  async getBibleDevotionalBook(
    bookId: number,
    day?: string,
  ): Promise<{ day: string; entry: Record<string, unknown> }> {
    const resp = await apiClient.get(`/api/bible/devotional/book/${bookId}`, {
      params: day ? { day } : undefined,
    });
    return resp.data;
  },

  async listBibleStudySessions(): Promise<{ sessions: BibleStudySession[]; count: number }> {
    const resp = await apiClient.get('/api/bible/study/sessions');
    return resp.data;
  },

  async searchBibleStudyMemory(query: string): Promise<BibleStudyMemoryResponse> {
    const resp = await apiClient.get('/api/bible/study/sessions/search', {
      params: { q: query },
    });
    return resp.data;
  },

  async createBibleStudySession(payload: {
    reference: string;
    version?: string;
    edition?: string;
    title?: string;
  }): Promise<{ session: BibleStudySession }> {
    const resp = await apiClient.post('/api/bible/study/sessions', payload);
    return resp.data;
  },

  async getBibleStudySession(sessionId: number): Promise<{ session: BibleStudySession }> {
    const resp = await apiClient.get(`/api/bible/study/sessions/${sessionId}`);
    return resp.data;
  },

  async addBibleStudyTurn(
    sessionId: number,
    payload: { question: string; answer: string; references: string[] },
  ): Promise<{ turn: Record<string, unknown> }> {
    const resp = await apiClient.post(`/api/bible/study/sessions/${sessionId}/turn`, payload);
    return resp.data;
  },

  async deleteBibleStudySession(sessionId: number): Promise<{ status: string; id: number }> {
    const resp = await apiClient.delete(`/api/bible/study/sessions/${sessionId}`);
    return resp.data;
  },

  /** Which books have an imported interlinear (admin). */
  async getBibleInterlinearCoverage(): Promise<BibleInterlinearCoverage> {
    const resp = await apiClient.get('/api/bible/admin/interlinear');
    return resp.data;
  },

  /** Import a STEPBible TAGNT/TAHOT tagged text (admin). */
  async uploadBibleInterlinear(file: File): Promise<BibleInterlinearImport> {
    const form = new FormData();
    form.append('file', file, file.name);
    const resp = await apiClient.post('/api/bible/admin/interlinear/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 600_000,
    });
    return resp.data;
  },

  /** What installing this translation online would cost, before any of it is spent. */
  async getBibleProviderEstimate(
    code: string,
    translationId: string,
  ): Promise<BibleProviderEstimate> {
    const resp = await apiClient.get(`/api/bible/admin/providers/${code}/estimate`, {
      params: { translation_id: translationId },
    });
    return resp.data;
  },

  /**
   * List one folder of the Nextcloud book library.
   *
   * A POST because the folder is operator input: WebDAV names carry slashes and
   * can carry semicolons, so a query string is the wrong place for one.
   */
  async getBibleLibrary(path?: string): Promise<BibleLibraryListing> {
    const resp = await apiClient.post('/api/bible/admin/library', { path: path ?? '' });
    return resp.data;
  },

  /** A music suggestion for the signed-in member, derived from their listening. */
  async getMusicRecommendation(): Promise<{ status: string; recommendation: { lane: string; artist: string; style: string; reason: string; matched_on: string; kind: string } | null }> {
    const resp = await apiClient.get('/api/recommendations/music');
    return resp.data;
  },

  /** A video/creator suggestion for the signed-in member, derived from their watching. */
  async getVideoRecommendation(): Promise<{ status: string; recommendation: { theme: string; creator: string | null; style: string; reason: string; matched_on: string; kind: string; redeemed: boolean; redirect_to?: string } | null }> {
    const resp = await apiClient.get('/api/recommendations/video');
    return resp.data;
  },

  /** A podcast suggestion for the signed-in member, derived from their listening. */
  async getPodcastRecommendation(): Promise<{ status: string; recommendation: { theme: string; show: string | null; host: string | null; style: string; reason: string; matched_on: string; kind: string; redeemed: boolean; redirect_to?: string } | null }> {
    const resp = await apiClient.get('/api/recommendations/podcast');
    return resp.data;
  },

  /** A book suggestion for the signed-in member, derived from their reading. */
  async getGameRecommendation(): Promise<{ status: string; recommendation: { theme: string; game: string | null; style: string; reason: string; matched_on: string; kind: string; redeemed: boolean; redirect_to?: string } | null }> {
    const resp = await apiClient.get('/api/recommendations/game');
    return resp.data;
  },

  async getActivityRecommendation(): Promise<{ status: string; recommendation: { theme: string; activity: string; style: string; reason: string; matched_on: string; kind: string } | null }> {
    const resp = await apiClient.get('/api/recommendations/activity');
    return resp.data;
  },

  async getBookRecommendation(): Promise<{ status: string; recommendation: { theme: string; title: string | null; author: string | null; style: string; reason: string; matched_on: string; kind: string; redeemed: boolean; redirect_to?: string } | null }> {
    const resp = await apiClient.get('/api/recommendations/book');
    return resp.data;
  },
};

/** Notice category to on/off; a category that is absent is on. */
/** One waiting place reminder, as geo keeps it (docs/NOTES.md, Reminders (place)). */
export interface PlaceReminder {
  id: string;
  zone: string;
  zone_name: string;
  on: 'arrive' | 'leave';
  title: string;
  url: string | null;
  created_at: number;
}

export type NotificationPreferences = Record<string, boolean>;

/** One app on one phone using Jarvis for push (GET /api/push/up). */
export interface PushAppRegistration {
  /** Android package name, e.g. com.nextcloud.talk2. */
  app: string;
  token?: string;
  topic?: string;
  endpoint?: string;
  device_id?: string;
  created_at?: string;
}

function booleanMap(value: unknown): NotificationPreferences {
  const out: NotificationPreferences = {};
  if (!value || typeof value !== 'object') return out;
  for (const [key, on] of Object.entries(value as Record<string, unknown>)) {
    if (typeof on === 'boolean') out[key] = on;
  }
  return out;
}

export interface RavenLearningItem {
  id: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: number;
  usage_count: number;
  last_used_at: string | null;
  // Structured, honest lesson fields (true source of truth).
  rule: string;
  root_cause: string;
  outcome: string; // 'success' | 'partial' | 'failure'
  confidence: number;
  applied_count: number;
  supersedes: string[];
}

export interface RavenLearningsResponse {
  status: string;
  count: number;
  items: RavenLearningItem[];
}

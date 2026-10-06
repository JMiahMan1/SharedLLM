import axios, { type AxiosRequestConfig } from 'axios';
import { storageGetSync } from '../lib/storage';
import { Capacitor } from '@capacitor/core';
import type { DeviceSortMode, WidgetVisibility, WidgetSize, DeviceEntry, CalendarPerson } from '../types/widget';
import type {
  HealthStatus,
  ServiceInfo,
  LogEntry,
  Workspace,
  WorkspaceListResponse,
  UserProfileRaw,
  UserProfile,
  CredentialShares,
  ShareableService,
  APIKey,
  DiscoveredUser,
  DeviceAssignment,
  EntityProtection,
  ShareRecipient,
  AiCapability,
  GlobalSetting,
  GatewayConfig,
  EsphomeDevice,
  CompanionDevice,
  PairDeviceRequest,
  ExecutionResponse,
  ArcadeGamesResponse,
  TimerRecord,
  SmokeTestResult,
  StorageEntry,
  RagStats,
  RavenMission,
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
  WorkspaceFileWriteResponse,
  WorkspaceFileListResponse,
  GitStatusResponse,
  GitBranchesResponse,
  GitCheckoutResponse,
  GitDiffResponse,
  GitCommitResponse,
  GitPushResponse,
  GitLogResponse,
  StorageMirrorResponse,
  PytestRequest,
  PytestResponse,
  WorkflowWriteSyncCommitRequest,
  VolumesResponse,
  ServiceLogsResponse,
  ContainerExecResponse,
  RagIndexedPathsResponse,
  RagSyncFilesRequest,
  RagSyncCapabilitiesRequest,
  WorkspaceShellRequest,
  WorkspaceShellResponse,
  WorkspaceFilePatchExecuteRequest,
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
  BiblePassage,
  BibleSearchResult,
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
  BibleActivitySummary,
  BibleActivityFeed,
  BibleEditionsResponse,
  BibleBlbLink,
  BibleStudyNoteKind,
  BibleStudyNotesResponse,
  BibleDailyResponse,
  BibleVoicesResponse,
  BibleNarration,
  BibleImportKind,
  BibleImportRun,
  BibleImportsResponse,
  BibleImportProviderInfo,
  BibleRemoteTranslation,
  BibleProviderEstimate,
} from '../types/api';

// Re-export domain types so consumers can import them from the api module.
export type {
  APIKey,
  GlobalSetting,
  HealthStatus,
  EsphomeDevice,
  CompanionDevice,
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
  EntityProtection,
  ShareRecipient,
  AiCapability,
  DiscoveredUser,
  RagStats,
  TelemetryEnrollment,
  ExecutionResponse,
  ArcadeGamesResponse,
  TimerRecord,
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
     * stored session or redirect to /login — otherwise a call that races
     * authentication can log the user out (and loop).
     */
    skipAuthRedirect?: boolean;
  }
  export interface AxiosRequestConfig {
    skipAuthRedirect?: boolean;
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

const mapUserPayload = (data: Partial<UserProfile>) => {
  const payload: Record<string, unknown> = { ...data };
  if ('full_name' in payload) {
    payload.display_name = payload.full_name;
    delete payload.full_name;
  }
  if ('voice_id' in payload) {
    payload.voice_fingerprint = payload.voice_id;
    delete payload.voice_id;
  }
  return payload;
};

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
  const internalSecret = storageGetSync('internal_secret');

  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  if (internalSecret) {
    config.headers['X-Internal-Secret'] = internalSecret;
  }

  return config;
});

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
function describeFailedTarget(config: AxiosRequestConfig | undefined, status?: number, code?: string): string {
  const method = (config?.method || 'GET').toString().toUpperCase();
  const path: string = config?.url?.toString() || '(unknown endpoint)';
  let service = 'Jarvis server';
  if (path.startsWith('/api/execute')) service = 'Execution service';
  else if (path.startsWith('/api/media')) service = 'Media service';
  else if (path.startsWith('/api/auth') || path.startsWith('/api/users')) service = 'Identity service';
  else if (path.includes('ma-jsonrpc') || path.includes('sendspin')) service = 'Music Assistant';
  else if (path.startsWith('/api/')) service = 'Gateway';

  const detail = status ? ` (HTTP ${status})` : code ? ` (${code})` : '';
  const target = `${service}${detail} — ${method} ${path}`;
  lastFailedTarget = target;
  return target;
}

/**
 * Re-throw an upstream failure carrying the server's own explanation.
 *
 * The response interceptor rejects the raw axios error, whose `message` is
 * only ever "Request failed with status code 400" — it drops the `detail` the
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
    const isRetryable = isAxiosError && (
      (status && [408, 503, 504].includes(status)) ||
      (code && ['ECONNABORTED', 'ERR_NETWORK', 'ECONNREFUSED', 'ENOTFOUND'].includes(code))
    );

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

    if (isConnectivityError) {
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
      // the session — otherwise the app bounces to /login on every start.
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
  async getSharingRecipients(): Promise<ShareRecipient[]> {
    const resp = await apiClient.get('/api/users/sharing-recipients');
    return resp.data ?? [];
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

  async enrollVoice(audioBlob: Blob): Promise<{ status: string; message: string }> {
    const formData = new FormData();
    formData.append('file', audioBlob, 'enrollment.webm');
    const resp = await apiClient.post('/api/users/me/enroll', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return resp.data;
  },

  async getHealth(): Promise<HealthStatus> {
    const resp = await apiClient.get('/health/ready');
    return resp.data;
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
    const resp = await apiClient.post('/api/chat', {
      query: message,
      workspace_id: workspaceId,
      user_id: userId,
      stream,
    });
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

  async getLogs(limit = 50): Promise<LogEntry[]> {
    const resp = await apiClient.get(`/api/logs?limit=${limit}`);
    return resp.data;
  },

  async clearLogs(): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete('/api/logs');
    return resp.data;
  },

  getLogWebSocketUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const token = storageGetSync('jarvis_api_key') || '';
    return `${protocol}//${host}/api/logs/stream?token=${encodeURIComponent(token)}`;
  },

  /**
   * @deprecated Use useWebSocket + getLogWebSocketUrl() instead
   */
  getLogWebSocket(): WebSocket {
    return new WebSocket(this.getLogWebSocketUrl());
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
  async getVehicles(): Promise<{ vehicles: Array<{ id: string; name: string; mpg: number; cost_per_gallon: number; fuel_type: string }> }> {
    const resp = await apiClient.get('/api/geo/vehicles');
    return resp.data;
  },

  async saveVehicle(vehicle: { id: string; name: string; mpg: number; cost_per_gallon: number; fuel_type?: string }): Promise<{ status: string; vehicle: Record<string, unknown> }> {
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

  // Family Circle & Vehicle Trips
  async getTrips(userId?: string): Promise<TripsResponse> {
    const resolved = resolveUserId(userId);
    const query = resolved ? `?user_id=${encodeURIComponent(resolved)}` : '';
    const resp = await apiClient.get(`/api/geo/trips${query}`);
    return resp.data;
  },

  /**
   * Last known GPS position for every user whose app has location sharing on.
   * Users with tracking off simply have no entry (or a stale one).
   */
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

  async generateMusic(payload: {
    prompt: string;
    duration_s?: number;
  }): Promise<{ status: string; audio_url?: string; mime?: string; message?: string }> {
    // Music generation can take minutes on CPU; the shared client's 15s would
    // abort it mid-render.
    const resp = await apiClient.post('/api/music/generate', payload, { timeout: 600_000 });
    return resp.data;
  },

  async joinTalkCall(token: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/call', { token, as_user });
    return resp.data;
  },

  async leaveTalkCall(token: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/call', { token, leave: true, as_user });
    return resp.data;
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

  // Activity trends — reading data never generates an analysis.
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

  async updateWorkspace(id: string, data: Partial<Workspace>): Promise<Workspace> {
    const resp = await apiClient.patch(`/api/workspaces/${id}`, data);
    return resp.data.workspace;
  },

  async deleteWorkspace(id: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/workspaces/${id}`);
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

  async updateDeviceAssignment(assignment: { username: string; device_id: string }): Promise<DeviceAssignment> {
    const resp = await apiClient.post('/api/users/devices', assignment);
    return resp.data;
  },

  async deleteDeviceAssignment(deviceId: string): Promise<{ status?: string; success?: boolean }> {
    const resp = await apiClient.delete(`/api/devices/${encodeURIComponent(deviceId)}`);
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
   * entirely — the entity reverts to plain device-assignment rules. A refusal
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

  async createTimer(payload: {
    title: string;
    duration_str?: string;
    time_str?: string;
    type?: 'timer' | 'alarm';
    recurrence?: string;
    target_device?: string;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/timers', payload);
    return resp.data;
  },

  async deleteTimer(title: string, type: 'timer' | 'alarm' = 'timer', id?: string): Promise<ExecutionResponse> {
    const data: { title: string; type: 'timer' | 'alarm'; id?: string } = { title, type };
    if (id) data.id = id;
    const resp = await apiClient.delete('/api/communication/timers', { data });
    return resp.data;
  },

  async timerAction(
    action: 'pause' | 'resume',
    payload: { title: string; type?: 'timer' | 'alarm'; id?: string }
  ): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/timers', { action, ...payload });
    return resp.data;
  },

  async getCalendarList(): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/calendar/calendars');
    return resp.data;
  },

  async getCalendarEvents(calendar_name?: string, integration?: string): Promise<ExecutionResponse> {
    const params: Record<string, string> = {};
    if (calendar_name) params.calendar_name = calendar_name;
    if (integration) params.integration = integration;
    const resp = await apiClient.get('/api/communication/calendar/events', { params });
    return resp.data;
  },

  async addCalendarEvent(payload: { summary: string; start_time: string; calendar_name?: string; integration?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/calendar/events', payload);
    return resp.data;
  },

  async updateCalendarEvent(payload: { action: 'update'; event_id?: string; integration?: string; query?: string; summary: string; start_time?: string }): Promise<ExecutionResponse> {
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

  async updateCalendarSettings(payload: { default?: string; disabled?: string[]; priority?: Record<string, number>; ical_urls?: string[]; people?: CalendarPerson[] }): Promise<ExecutionResponse> {
    const resp = await apiClient.put('/api/calendar/settings', payload);
    return resp.data;
  },

  async createNote(payload: { title: string; content?: string; category?: string; storage?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/create', payload);
    return resp.data;
  },

  async readNote(title: string, storage?: string, path?: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/read', { title, storage, path, as_user });
    return resp.data;
  },

  async appendNote(payload: { title: string; content: string; storage?: string; path?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/append', payload);
    return resp.data;
  },

  /** Full replace — the editor's Save. Append would duplicate the body. */
  async writeNote(payload: {
    title: string;
    content: string;
    category?: string;
    storage?: string;
    path?: string;
    as_user?: 'admin';
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/write', payload);
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
    const resp = await apiClient.post('/api/communication/notes/check_off', payload);
    return resp.data;
  },

  async deleteNote(title: string, storage?: string, path?: string, as_user?: 'admin'): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/delete', { title, storage, path, as_user });
    return resp.data;
  },

  async listNotes(payload: { storage?: string; directories?: string[]; as_user?: 'admin' } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/list', payload);
    return resp.data;
  },

  async syncNotesRag(payload: { storage?: string; directories?: string[] } = {}): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/sync_rag', payload);
    return resp.data;
  },

  async sendAnnouncement(payload: { entity_id: string; message: string; volume?: number }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/announcements', payload);
    return resp.data;
  },

  async getTalkConversations(): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/talk/conversations');
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

  async sendTalkMessage(payload: { token: string; message: string; as_user?: 'admin' }): Promise<ExecutionResponse> {
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

  async getArcadeGames(): Promise<ArcadeGamesResponse> {
    const resp = await apiClient.get('/api/arcade/games');
    return resp.data;
  },

  async setArcadeFeatured(slugs: string[]): Promise<ExecutionResponse> {
    const resp = await apiClient.put('/api/arcade/featured', { slugs });
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

  async triggerFullIndex(provider: { kind: string; settings: Record<string, unknown> }, options?: {
    path?: string;
    recursive?: boolean;
    user_id?: string;
    force?: boolean;
  }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/storage/index/full', {
      provider,
      path: options?.path ?? '/',
      recursive: options?.recursive ?? true,
      user_id: options?.user_id,
      force: options?.force ?? false,
    });
    return resp.data;
  },

  async getRagStats(): Promise<RagStats> {
    const resp = await apiClient.get('/api/storage/stats');
    return resp.data;
  },

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async getCollectionDocs(collectionName: string, limit: number = 100): Promise<any> {
    const resp = await apiClient.get(`/api/storage/collection/${collectionName}?limit=${limit}`);
    return resp.data;
  },

  async purgeRagCollection(collectionName: string, userId: string, filter?: Record<string, unknown>): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post(`/api/storage/purge/${collectionName}`, { user_id: userId, filter });
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
    return resp.data;
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

  async getWorkspaceRavenMissions(workspaceId: string): Promise<RavenMission[]> {
    const resp = await apiClient.get(`/api/workspaces/${encodeURIComponent(workspaceId)}/raven/missions`);
    return resp.data;
  },

  async createUserMission(query: string, priority = 1): Promise<{ status: string; mission: RavenMission }> {
    const resp = await apiClient.post('/api/raven/missions', { query, priority });
    return resp.data;
  },

  async createWorkspaceMission(workspaceId: string, query: string, priority = 3): Promise<{ status: string; mission: RavenMission }> {
    const resp = await apiClient.post('/api/raven/missions', { query, priority, workspace_id: workspaceId });
    return resp.data;
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

  async getMediaGroups(): Promise<MediaGroup[]> {
    const resp = await apiClient.get('/api/groups/media');
    return resp.data.groups || [];
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
    return resp.data.clusters || [];
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
    return resp.data.patterns || [];
  },

  async createLightPattern(data: { name: string; steps: Array<{ brightness?: number; color_temp?: number; rgb_color?: number[]; transition?: number; delay?: number }> }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/groups/patterns', data);
    return resp.data;
  },

  async deleteLightPattern(name: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/groups/patterns/${encodeURIComponent(name)}`);
    return resp.data;
  },

  async executeLightPattern(data: { pattern_name: string; target_cluster?: string; target_entity_ids?: string[] }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/execute/groups/lights', data);
    return resp.data;
  },

  async getTelemetryEnrollments(): Promise<TelemetryEnrollment[]> {
    const resp = await apiClient.get('/api/telemetry/enroll');
    return resp.data.enrollments || [];
  },

  async analyzeTelemetry(entityId: string, hours = 168): Promise<{ status: string; message: string }> {
    // entity_id is required by the analysis endpoint — without it the call 400s.
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
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/transport', payload);
    return resp.data;
  },

  async mediaStatus(): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/status', {});
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
   * Rejects when MA does not have the book — callers must surface that rather
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

  async getMusicAssistantPlaylists(): Promise<{ status: string; playlists: Array<{ name: string; items: number; uri: string }> }> {
    try {
      const resp = await apiClient.get('/api/media/music-assistant/playlists', { timeout: 6000 });
      return resp.data;
    } catch {
      return { status: 'ERROR', playlists: [] };
    }
  },

  async getMusicAssistantRecent(): Promise<{ status: string; recent: Array<{ name: string; artist: string; uri: string; last_played: string }> }> {
    try {
      const resp = await apiClient.get('/api/media/music-assistant/recent', { timeout: 6000 });
      return resp.data;
    } catch {
      return { status: 'ERROR', recent: [] };
    }
  },

  async getAudiobookshelfLibraries(): Promise<{ status: string; libraries: Array<{ id: string; name: string; media_type: string }> }> {
    try {
      const resp = await apiClient.get('/api/media/audiobookshelf/libraries', { timeout: 6000 });
      return resp.data;
    } catch {
      return { status: 'ERROR', libraries: [] };
    }
  },

  async getAudiobookshelfLastPlayed(): Promise<{ status: string; books: Array<{ id: string; title: string; author: string; progress: number; last_played: string; library_id: string }> }> {
    try {
      const resp = await apiClient.get('/api/media/audiobookshelf/last-played', { timeout: 6000 });
      return resp.data;
    } catch {
      return { status: 'ERROR', books: [] };
    }
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

  async playPlaylist(payload: {
    playlist_uri: string;
    entity_id?: string;
    device_name?: string;
    volume?: number;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/execute/media/play', {
      query: payload.playlist_uri,
      media_type: 'music',
      entity_id: payload.entity_id,
      device_name: payload.device_name,
      volume: payload.volume,
    });
    return resp.data;
  },

  async getAudiobookshelfLibrary(libraryId: string, limit = 50): Promise<{ status: string; books: Array<{ id: string; title: string; author: string }> }> {
    try {
      const resp = await apiClient.get(`/api/media/audiobookshelf/library/${encodeURIComponent(libraryId)}?limit=${limit}`, { timeout: 6000 });
      return resp.data;
    } catch {
      return { status: 'ERROR', books: [] };
    }
  },

  async searchAudiobookshelf(query: string, limit = 20): Promise<{
    status: string;
    books: Array<{ id: string; title: string; author: string; narrator?: string; cover?: string; genres?: string[]; publishedYear?: string; asin?: string; type?: string; source?: string; duration?: number; duration_formatted?: string; series?: string; play_url?: string; progress?: number | null }>;
    podcasts: Array<{ id: string; title: string; author: string; description?: string; cover?: string; trackCount?: number; genres?: string[]; explicit?: boolean; type?: string; source?: string }>;
    authors: Array<{ id: string; name: string; description?: string; image?: string; asin?: string; type?: string; source?: string }>;
    total?: number;
  }> {
    const resp = await apiClient.get(`/api/media/audiobookshelf/search?q=${encodeURIComponent(query)}&limit=${limit}`);
    return resp.data;
  },

  async searchMusicAssistant(query: string, mediaType?: string, limit = 30, libraryOnly = true): Promise<{
    status: string;
    results: Array<{ name: string; uri: string; type: string; duration?: number; artist?: string; album?: string; cover?: string; trackCount?: number }>;
  }> {
    const params = new URLSearchParams({ query, limit: String(limit), library_only: String(libraryOnly) });
    if (mediaType) params.set('media_type', mediaType);
    const resp = await apiClient.get(`/api/media/music-assistant/search?${params.toString()}`);
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

  async editImage(payload: { prompt: string; image: string; model?: string }): Promise<{ status: string; data?: Array<{ url?: string; b64_json?: string }>; message?: string }> {
    const resp = await apiClient.post('/api/images/edit', payload);
    return resp.data;
  },

  // Workspace-scoped AI image edit (execution service path). Uses a dedicated
  // long-timeout request: CPU-offloaded image editing takes minutes, and the
  // shared apiClient aborts at 15s.
  //
  // A face swap is a two-image edit: `image_path` is the photo to keep and
  // `face_image_path` is the donor face copied onto it. The server rejects a
  // swap prompt with no donor, so both images are required for a swap.
  async workspaceEditImage(workspaceId: string, payload: { prompt: string; image_path: string; face_image_path?: string; output_path?: string; model?: string; size?: string }): Promise<{ status: string; message?: string; detail?: { output_path?: string; face_swapped?: boolean; face_image_path?: string | null } }> {
    let baseURL = getBaseUrl();
    if (Capacitor.isNativePlatform()) {
      const serverUrl = storageGetSync('jarvis_server_url');
      if (serverUrl) baseURL = serverUrl;
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = storageGetSync('jarvis_api_key');
    const internalSecret = storageGetSync('internal_secret');
    if (token) headers.Authorization = `Bearer ${token}`;
    if (internalSecret) headers['X-Internal-Secret'] = internalSecret;
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
    let baseURL = getBaseUrl();
    if (Capacitor.isNativePlatform()) {
      const serverUrl = storageGetSync('jarvis_server_url');
      if (serverUrl) baseURL = serverUrl;
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = storageGetSync('jarvis_api_key');
    const internalSecret = storageGetSync('internal_secret');
    if (token) headers.Authorization = `Bearer ${token}`;
    if (internalSecret) headers['X-Internal-Secret'] = internalSecret;
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

  // Workspace -> NextCloud sync (mirror local_path -> nextcloud remote_path)
  async syncWorkspaceNextcloud(payload: { remote_path: string; local_path: string; excludes?: string[] }): Promise<StorageMirrorResponse> {
    const resp = await apiClient.post('/api/storage/mirror', payload);
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

  // Forget a workspace's sync history (after pointing it at another folder by hand).
  async resetWorkspaceSync(workspaceId: string): Promise<{ status: string }> {
    const resp = await apiClient.post('/api/workspaces/sync/reset', { workspace_id: workspaceId, confirm: true });
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

  async runPytest(request: PytestRequest): Promise<PytestResponse> {
    const resp = await apiClient.post('/api/workspaces/tests/pytest', request);
    return resp.data;
  },

  async runWorkflowWriteSyncCommit(request: WorkflowWriteSyncCommitRequest): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/workspaces/workflow/write-sync-commit', request);
    return resp.data;
  },

  async resolveWorkspace(workspaceId: string): Promise<{ status: string; resolved_path: string }> {
    const resp = await apiClient.post('/api/workspaces/resolve', { workspace_id: workspaceId });
    return resp.data;
  },

  async bootstrapWorkspace(workspaceId: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/workspaces/bootstrap', { workspace_id: workspaceId });
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

  async syncRagCapabilities(request: RagSyncCapabilitiesRequest): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/rag/sync/capabilities', request);
    return resp.data;
  },

  // Execution service endpoints
  async workspaceShell(request: WorkspaceShellRequest): Promise<WorkspaceShellResponse> {
    const resp = await apiClient.post('/execute/workspace_shell', request);
    return resp.data;
  },

  async workspaceFileRead(workspaceId: string, path: string): Promise<WorkspaceFileReadResponse> {
    const resp = await apiClient.get('/execute/workspace_file_read', { params: { workspace_id: workspaceId, path } });
    return resp.data;
  },

  async workspaceFileWrite(workspaceId: string, path: string, content: string): Promise<WorkspaceFileWriteResponse> {
    const resp = await apiClient.post('/execute/workspace_file_write', { workspace_id: workspaceId, path, content });
    return resp.data;
  },

  async workspaceFilePatch(request: WorkspaceFilePatchExecuteRequest): Promise<WorkspaceFileWriteResponse> {
    const resp = await apiClient.post('/execute/workspace_file_patch', request);
    return resp.data;
  },

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
  async getBibleDaily(): Promise<BibleDailyResponse> {
    const resp = await apiClient.get('/api/bible/daily');
    return resp.data;
  },

  async getBibleVersions(): Promise<{ versions: BibleVersionInfo[] }> {
    const resp = await apiClient.get('/api/bible/versions');
    return resp.data;
  },

  async getBibleBooks(version?: string): Promise<{ version: string; books: BibleBookInfo[] }> {
    const resp = await apiClient.get('/api/bible/books', { params: { version } });
    return resp.data;
  },

  async getBiblePassage(ref: string, version?: string): Promise<BiblePassage> {
    const resp = await apiClient.get('/api/bible/passages', { params: { ref, version } });
    return resp.data;
  },

  async searchBible(q: string, options: { version?: string; book?: string; limit?: number } = {}): Promise<BibleSearchResult> {
    const resp = await apiClient.get('/api/bible/search', { params: { q, ...options } });
    return resp.data;
  },

  async getBibleVerseOfDay(params: { day?: string; version?: string; scope?: 'all' | 'ot' | 'nt' } = {}): Promise<BibleVerseOfDay> {
    const resp = await apiClient.get('/api/bible/verse-of-day', { params });
    return resp.data;
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
    kind?: 'highlight' | 'bookmark';
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
    const resp = await apiClient.get('/api/bible/state');
    return resp.data;
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

  async getBibleActivitySummary(userId: string): Promise<BibleActivitySummary> {
    const resp = await apiClient.get('/api/bible/activity/summary', { params: { user_id: userId } });
    return resp.data;
  },

  async getBibleActivityFeed(limit = 20): Promise<BibleActivityFeed> {
    const resp = await apiClient.get('/api/bible/activity/feed', { params: { limit } });
    return resp.data;
  },

  async getBlbLink(ref: string, tool?: string): Promise<BibleBlbLink> {
    const resp = await apiClient.get('/api/bible/blb/link', { params: { ref, tool } });
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
    const resp = await apiClient.get('/api/bible/narration', { params: { ref, version, voice } });
    return resp.data;
  },

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
};

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

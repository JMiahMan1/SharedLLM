import axios from 'axios';
import { storageGetSync } from '../lib/storage';
import { Capacitor } from '@capacitor/core';
import type { DeviceSortMode, WidgetVisibility, WidgetSize, DeviceEntry } from '../types/widget';

function getBaseUrl(): string {
  if (Capacitor.isNativePlatform()) {
    return 'http://localhost';
  }
  return window.location.origin;
}

export interface HealthStatus {
  status: 'READY' | 'NOT_READY';
  services: Record<string, string>;
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

type WorkspaceListResponse =
  | Workspace[]
  | {
      status?: string;
      workspaces?: Workspace[];
    };

interface UserProfileRaw {
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

export interface MediaGroup { name: string; member_entity_ids?: string[] }
export interface LightCluster { name: string; member_entity_ids?: string[] }
export interface LightPattern { name: string; steps?: unknown[] }
export interface TelemetryEnrollment { entity_id: string; power_tracking: boolean; availability_tracking: boolean; offline_alert_threshold_minutes: number }
export interface IntercomSessionData { session_id: string; caller_user_id: string; target_user_id?: string; target_room?: string; session_type: string; status: string }
export interface IntercomConfigData { default_tts_engine?: string; default_voice?: string; default_volume?: number; enable_espresense_routing?: boolean }

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

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const isAxiosError = axios.isAxiosError(error);
    const isConnectivityError = isAxiosError && (
      error.code === 'ECONNABORTED' ||
      error.code === 'ENOTFOUND' ||
      error.code === 'ECONNREFUSED' ||
      error.code === 'ERR_NETWORK'
    );

    if (isConnectivityError && Capacitor.isNativePlatform()) {
      const now = Date.now();
      if (now - lastConnectivityToast > 15000) {
        lastConnectivityToast = now;
        import('react-hot-toast').then(({ toast: t }) => {
          t.error('Cannot connect to Jarvis server. Check your network connection.', {
            duration: 8000,
            style: {
              background: 'rgba(239, 68, 68, 0.2)',
              color: '#fca5a5',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              fontSize: '12px',
            },
          });
        });
      }
    }

    console.error('[API] Response error:', error.message, error.config?.baseURL, error.config?.url);
    if (error.response?.status === 401 && !isLoggingOut) {
      const isLoginRequest = error.config?.url?.includes('/api/auth/login');
      if (!isLoginRequest) {
        isLoggingOut = true;
        import('../lib/storage').then(({ storageRemove }) => {
          storageRemove('jarvis_api_key');
          storageRemove('jarvis_user');
        });
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

  async globalSearch(query: string): Promise<unknown> {
    const resp = await apiClient.get(`/api/search?q=${encodeURIComponent(query)}`);
    return resp.data;
  },

  async getLogs(limit = 50): Promise<LogEntry[]> {
    const resp = await apiClient.get(`/api/logs?limit=${limit}`);
    return resp.data;
  },

  async clearLogs(): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete('/api/logs');
    return resp.data;
  },

  getLogWebSocket(): WebSocket {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const token = storageGetSync('jarvis_api_key') || '';
    return new WebSocket(`${protocol}//${host}/api/logs/stream?token=${encodeURIComponent(token)}`);
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

  async getDnsConfig(): Promise<{ dns_mappings: Record<string, string>; dns_upstream: string; dns_poll_interval: number }> {
    const resp = await apiClient.get('/api/admin/dns');
    return resp.data;
  },

  async updateDnsConfig(config: { dns_mappings?: Record<string, string>; dns_upstream?: string; dns_poll_interval?: number }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/admin/dns/update', config);
    return resp.data;
  },

  async registerDnsEntry(hostname: string, ip: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/admin/dns/register', { hostname, ip });
    return resp.data;
  },

  async removeDnsEntry(hostname: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/admin/dns/${hostname}`);
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

  async pullWorkspace(id: string, branch?: string): Promise<{ status: string; message: string; branch: string }> {
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

  async deleteTimer(title: string, type: 'timer' | 'alarm' = 'timer'): Promise<ExecutionResponse> {
    const resp = await apiClient.delete('/api/communication/timers', { data: { title, type } });
    return resp.data;
  },

  async getCalendarList(): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/calendar/calendars');
    return resp.data;
  },

  async getCalendarEvents(calendar_name?: string): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/calendar/events', { params: { calendar_name } });
    return resp.data;
  },

  async addCalendarEvent(payload: { summary: string; start_time: string; calendar_name?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/calendar/events', payload);
    return resp.data;
  },

  async createNote(payload: { title: string; content?: string; category?: string; storage?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/create', payload);
    return resp.data;
  },

  async readNote(title: string, storage?: string, path?: string): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/read', { title, storage, path });
    return resp.data;
  },

  async appendNote(payload: { title: string; content: string; storage?: string; path?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/append', payload);
    return resp.data;
  },

  async deleteNote(title: string, storage?: string, path?: string): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/delete', { title, storage, path });
    return resp.data;
  },

  async listNotes(payload: { storage?: string; directories?: string[] } = {}): Promise<ExecutionResponse> {
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

  async openTalkConversation(payload: { token?: string; target_user?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/conversations/open', payload);
    return resp.data;
  },

  async getTalkMessages(token: string, limit = 50): Promise<ExecutionResponse> {
    const resp = await apiClient.get(`/api/communication/talk/messages?token=${encodeURIComponent(token)}&limit=${limit}`);
    return resp.data;
  },

  async sendTalkMessage(payload: { token: string; message: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/messages', payload);
    return resp.data;
  },

  async sendTalkVoice(payload: {
    token: string;
    audio_base64: string;
    mime_type?: string;
    file_name?: string;
    caption?: string;
  }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/talk/voice', payload);
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

  async createUserMission(query: string, priority = 1): Promise<{ status: string; mission: RavenMission }> {
    const resp = await apiClient.post('/api/raven/missions', { query, priority });
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

  async enrollTelemetry(data: { entity_id: string; offline_alert_threshold_minutes: number }): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/telemetry/enroll', {
      entity_id: data.entity_id,
      power_tracking: true,
      availability_tracking: true,
      usage_tracking: true,
      offline_alert_threshold_minutes: data.offline_alert_threshold_minutes,
    });
    return resp.data;
  },

  async unenrollTelemetry(entity_id: string): Promise<{ status: string; message: string }> {
    const resp = await apiClient.delete(`/api/telemetry/enroll/${encodeURIComponent(entity_id)}`);
    return resp.data;
  },

  async analyzeTelemetry(): Promise<{ status: string; message: string }> {
    const resp = await apiClient.post('/api/telemetry/analyze', { hours: 168 });
    return resp.data;
  },

  async getTelemetrySummary(entityId: string): Promise<{
    entity_id: string;
    summary: {
      current_power_w: number | null;
      peak_power_w: number | null;
      avg_power_w: number | null;
      availability_pct: number;
      total_activations: number;
      data_points: Array<{ recorded_at: number; power_w?: number; is_available?: boolean; state?: string; source?: string }>;
    } | null;
  }> {
    const resp = await apiClient.get(`/api/telemetry/summary/${encodeURIComponent(entityId)}`);
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

  async setMediaFavorite(uri: string, favorite: boolean): Promise<{ status: string; favorite: boolean }> {
    const resp = await apiClient.post('/api/media/favorite', { uri, favorite });
    return resp.data;
  },

  async getMusicAssistantPlaylists(): Promise<{ status: string; playlists: Array<{ name: string; items: number; uri: string }> }> {
    const resp = await apiClient.get('/api/media/music-assistant/playlists');
    return resp.data;
  },

  async getMusicAssistantRecent(): Promise<{ status: string; recent: Array<{ name: string; artist: string; uri: string; last_played: string }> }> {
    const resp = await apiClient.get('/api/media/music-assistant/recent');
    return resp.data;
  },

  async getAudiobookshelfLibraries(): Promise<{ status: string; libraries: Array<{ id: string; name: string; media_type: string }> }> {
    const resp = await apiClient.get('/api/media/audiobookshelf/libraries');
    return resp.data;
  },

  async getAudiobookshelfLastPlayed(): Promise<{ status: string; books: Array<{ id: string; title: string; author: string; progress: number; last_played: string; library_id: string }> }> {
    const resp = await apiClient.get('/api/media/audiobookshelf/last-played');
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
    const resp = await apiClient.get(`/api/media/audiobookshelf/library/${encodeURIComponent(libraryId)}?limit=${limit}`);
    return resp.data;
  },

  async searchAudiobookshelf(query: string, limit = 20): Promise<{ status: string; books: Array<{ id: string; title: string; author: string; narrator?: string; duration?: number }> }> {
    const resp = await apiClient.get(`/api/media/audiobookshelf/search?q=${encodeURIComponent(query)}&limit=${limit}`);
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

  async getSkylightChores(username?: string, date?: string): Promise<{
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
    }>;
  }> {
    const params = new URLSearchParams();
    if (username) params.set('user', username);
    if (date) params.set('date', date);
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
    const params = new URLSearchParams();
    if (domains && domains.length > 0) {
      params.set('domain', domains.join(','));
    }
    const query = params.toString();
    const resp = await apiClient.post(`/execute/entity/search${query ? `?${query}` : ''}`, { query: '', domain: null, area: null, state: null });
    return (resp.data.result || []).map((e: { entity_id: string; friendly_name: string; state: string; domain: string; area_id?: string }) => ({
      entity_id: e.entity_id,
      friendly_name: e.friendly_name,
      state: e.state,
      domain: e.domain,
      room: e.area_id || undefined,
      last_activated: undefined,
    }));
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

  // Mobile-local audio streaming
  async getAudiobookStreamUrl(bookId: string): Promise<string> {
    const resp = await apiClient.get(`/api/media/stream/audiobookshelf/${bookId}`, {
      responseType: 'text',
    });
    return resp.request.responseURL || '';
  },

  async getMusicAssistantStreamUrl(uri: string): Promise<string> {
    const resp = await apiClient.get('/api/media/stream/music-assistant', {
      params: { uri },
      responseType: 'text',
    });
    return resp.request.responseURL || '';
  },
};

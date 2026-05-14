import axios from 'axios';

const BASE_URL = '';

export interface HealthStatus {
  status: 'READY' | 'NOT_READY';
  services: Record<string, string>;
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
  auto_pull_enabled: boolean;
  webhook_token?: string | null;
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

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('jarvis_api_key');
  const internalSecret = localStorage.getItem('internal_secret');

  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  if (internalSecret) {
    config.headers['X-Internal-Secret'] = internalSecret;
  }

  return config;
});

let isLoggingOut = false;

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !isLoggingOut) {
      isLoggingOut = true;
      localStorage.removeItem('jarvis_api_key');
      localStorage.removeItem('jarvis_user');
      window.location.href = '/login';
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

  async discoverUsers(): Promise<DiscoveredUser[]> {
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
    return new WebSocket(`${protocol}//${host}/api/logs/stream`);
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

  async getCalendarEvents(): Promise<ExecutionResponse> {
    const resp = await apiClient.get('/api/communication/calendar/events');
    return resp.data;
  },

  async addCalendarEvent(payload: { summary: string; start_time: string; calendar_name?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/calendar/events', payload);
    return resp.data;
  },

  async createNote(payload: { title: string; content?: string; category?: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/create', payload);
    return resp.data;
  },

  async readNote(title: string): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/read', { title });
    return resp.data;
  },

  async appendNote(payload: { title: string; content: string }): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/append', payload);
    return resp.data;
  },

  async deleteNote(title: string): Promise<ExecutionResponse> {
    const resp = await apiClient.post('/api/communication/notes/delete', { title });
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
};

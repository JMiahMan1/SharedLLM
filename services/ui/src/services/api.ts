import axios from 'axios';

// SharedLLM Nexus API Service
const BASE_URL = ''; // Proxied via Vite

export interface HealthStatus {
  status: 'READY' | 'NOT_READY';
  services: Record<string, string>;
}

export interface LogEntry {
  id: number;
  timestamp: string;
  service: string;
  level: string;
  message: string;
  context?: Record<string, unknown>;
}

export interface Workspace {
  id: string;
  name: string;
  resolved_path: string;
  available: boolean;
  scope: string;
}

export interface UserProfile {
  id: string;
  username: string;
  role: 'admin' | 'user';
  full_name?: string;
  voice_id?: string;
  avatar_url?: string;
  is_admin?: boolean;
  is_system_default?: boolean;
  share_with_all?: boolean;
  [key: string]: unknown;
}

export interface APIKey {
  id: string;
  label: string;
  prefix: string;
  created_at: string;
}

// Axios Instance
export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request Interceptor
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('jarvis_token');
  const internalSecret = localStorage.getItem('internal_secret');

  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  
  if (internalSecret) {
    config.headers['X-Internal-Secret'] = internalSecret;
  }

  return config;
});

// Response Interceptor
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    return Promise.reject(error);
  }
);

export const api = {
  // Auth & Identity
  async login(username: string, password: string): Promise<{ api_key: string, username: string, is_admin: boolean }> {
    const resp = await apiClient.post('/api/auth/login', { username, password });
    return resp.data;
  },

  async getMe(): Promise<UserProfile> {
    const resp = await apiClient.get('/api/users/me');
    return resp.data;
  },

  async discoverUsers(): Promise<UserProfile[]> {
    const resp = await apiClient.get('/api/auth/discover');
    return resp.data;
  },

  async getUsers(): Promise<UserProfile[]> {
    const resp = await apiClient.get('/api/users');
    return resp.data;
  },

  async updateUser(username: string, data: Partial<UserProfile>): Promise<UserProfile> {
    const resp = await apiClient.patch(`/api/users/${username}`, data);
    return resp.data;
  },

  async deleteUser(username: string): Promise<{ success: boolean }> {
    const resp = await apiClient.delete(`/api/users/${username}`);
    return resp.data;
  },

  async updateProfile(data: Partial<UserProfile>): Promise<UserProfile> {
    const resp = await apiClient.patch('/api/users/me', data);
    return resp.data;
  },

  async enrollVoice(audioBlob: Blob): Promise<{ success: boolean, voice_id: string }> {
    const formData = new FormData();
    formData.append('file', audioBlob, 'enrollment.webm');
    const resp = await apiClient.post('/api/users/me/enroll', formData);
    return resp.data;
  },

  // Gateway & System
  async getHealth(): Promise<HealthStatus> {
    const resp = await apiClient.get('/health/ready');
    return resp.data;
  },

  async chat(message: string, workspaceId?: string, userId?: string, stream = false): Promise<unknown> {
    const resp = await apiClient.post('/api/chat', { 
      message, 
      workspace_id: workspaceId, 
      user_id: userId,
      stream 
    });
    return resp.data;
  },

  async globalSearch(query: string): Promise<unknown> {
    const resp = await apiClient.get(`/api/search?q=${encodeURIComponent(query)}`);
    return resp.data;
  },

  // Logs & JarvisLab
  async getLogs(limit = 50): Promise<LogEntry[]> {
    const resp = await apiClient.get(`/api/logs?limit=${limit}`);
    return resp.data;
  },

  getLogWebSocket(): WebSocket {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    return new WebSocket(`${protocol}//${host}/api/logs/stream`);
  },

  // Settings & Integrations
  async getSettings(): Promise<unknown[]> {
    const resp = await apiClient.get('/api/settings');
    return resp.data;
  },

  async updateSetting(key: string, value: string): Promise<{ success: boolean }> {
    const resp = await apiClient.patch(`/api/settings/${key}`, { value });
    return resp.data;
  },

  async testConnection(service: string, config: Record<string, unknown>): Promise<{ status: 'SUCCESS' | 'ERROR', message?: string }> {
    const resp = await apiClient.post('/api/auth/test-connection', { service, config });
    return resp.data;
  },

  async getWorkspaces(): Promise<Workspace[]> {
    const resp = await apiClient.get('/api/workspaces');
    return resp.data;
  },

  // Execution Service (Scheduler/Timer)
  async setTimer(duration: number, label: string): Promise<{ success: boolean }> {
    const resp = await apiClient.post('/api/execute/timer', { duration, label });
    return resp.data;
  },

  async scheduleTask(task: string, time: string): Promise<{ success: boolean }> {
    const resp = await apiClient.post('/api/execute/calendar', { task, time });
    return resp.data;
  },

  // API Key Management
  async getAPIKeys(): Promise<APIKey[]> {
    const resp = await apiClient.get('/api/users/me/keys');
    return resp.data;
  },

  async generateAPIKey(label: string): Promise<APIKey & { key: string }> {
    const resp = await apiClient.post('/api/users/me/keys', { label });
    return resp.data;
  },

  async revokeAPIKey(keyId: string): Promise<{ success: boolean }> {
    const resp = await apiClient.delete(`/api/users/me/keys/${keyId}`);
    return resp.data;
  },

  // Admin/Devices
  async updateDeviceAssignment(assignment: { user_id: string, entity_id: string }): Promise<{ success: boolean }> {
    const resp = await apiClient.post('/api/users/devices', assignment);
    return resp.data;
  },
  
  // Tests
  async runSmokeTest(): Promise<{ success: boolean, results: unknown }> {
    const resp = await apiClient.post('/api/admin/tests/smoke');
    return resp.data;
  }
};

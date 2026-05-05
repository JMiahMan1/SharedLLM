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
  context?: any;
}

export interface Workspace {
  id: string;
  name: string;
  resolved_path: string;
  available: boolean;
  scope: string;
}

const getHeaders = () => {
  const apiKey = localStorage.getItem('nexus_api_key');
  return {
    'Content-Type': 'application/json',
    ...(apiKey ? { 'Authorization': `Bearer ${apiKey}` } : {})
  };
};

export const api = {
  async login(username: string, password: string): Promise<any> {
    const resp = await fetch(`${BASE_URL}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) throw new Error('Invalid credentials');
    return resp.json();
  },

  async discoverUsers(): Promise<any[]> {
    const resp = await fetch(`${BASE_URL}/api/auth/discover`, {
      headers: getHeaders(),
    });
    if (!resp.ok) throw new Error('Discovery failed');
    return resp.json();
  },

  async changePassword(newPassword: string): Promise<any> {
    const resp = await fetch(`${BASE_URL}/api/auth/change-password?new_password=${encodeURIComponent(newPassword)}`, {
      method: 'POST',
      headers: getHeaders(),
    });
    if (!resp.ok) throw new Error('Failed to update password');
    return resp.json();
  },

  async getHealth(): Promise<HealthStatus> {
    const resp = await fetch(`${BASE_URL}/health/ready`);
    if (!resp.ok) throw new Error('Health check failed');
    return resp.json();
  },

  async getLogs(limit = 50): Promise<LogEntry[]> {
    const resp = await fetch(`${BASE_URL}/api/logs?limit=${limit}`, {
      headers: getHeaders(),
    });
    if (!resp.ok) throw new Error('Failed to fetch logs');
    return resp.json();
  },

  async chat(message: string, workspaceId?: string, stream = false) {
    const resp = await fetch(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify({ message, workspace_id: workspaceId, stream }),
    });
    if (!resp.ok) throw new Error('Chat request failed');
    return resp.json();
  },

  async getWorkspaces(): Promise<Workspace[]> {
    const resp = await fetch(`${BASE_URL}/api/workspaces`, {
      headers: getHeaders(),
    });
    if (!resp.ok) throw new Error('Failed to fetch workspaces');
    return resp.json();
  }
};

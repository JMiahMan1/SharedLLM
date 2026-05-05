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

export const api = {
  async getHealth(): Promise<HealthStatus> {
    const resp = await fetch(`${BASE_URL}/health/ready`);
    if (!resp.ok) throw new Error('Health check failed');
    return resp.json();
  },

  async getLogs(limit = 50): Promise<LogEntry[]> {
    const resp = await fetch(`${BASE_URL}/api/logs?limit=${limit}`);
    if (!resp.ok) throw new Error('Failed to fetch logs');
    return resp.json();
  },

  async chat(message: string, workspaceId?: string, stream = false) {
    const resp = await fetch(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, workspace_id: workspaceId, stream }),
    });
    if (!resp.ok) throw new Error('Chat request failed');
    return resp.json();
  },

  async getWorkspaces(): Promise<Workspace[]> {
    // Note: This endpoint might need to be exposed or called via chat discovery
    const resp = await fetch(`${BASE_URL}/api/workspaces`);
    if (!resp.ok) throw new Error('Failed to fetch workspaces');
    return resp.json();
  }
};

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import Admin from './Admin';

const mockApi = vi.hoisted(() => ({
  getSystemHealth: vi.fn(),
  checkAllUpdates: vi.fn(),
  pullAndRestart: vi.fn(),
  pullServiceImage: vi.fn(),
  getPullStatus: vi.fn(),
  restartService: vi.fn(),
  getServiceDefaults: vi.fn().mockResolvedValue({ urls: {}, source: {} }),
  getUsers: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({}),
}));

vi.mock('../services/api', () => ({ api: mockApi }));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'admin', is_admin: true }, isAuthenticated: true }),
}));
vi.mock('../hooks/useIntegrations', () => ({
  useIntegrations: () => ({ anyHub: true, homehub: true, homeAssistant: false, hubs: { homehub: true, homeAssistant: false }, settled: true }),
}));
vi.mock('../components/ui/ConfirmDialog', () => ({
  confirmDialog: vi.fn().mockResolvedValue(true),
}));
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn() },
}));

describe('Admin System Health service updates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getSystemHealth.mockResolvedValue({
      total_services: 2,
      running: 2,
      stopped: 0,
      unhealthy: 0,
      control_plane: { status: 'running', git_sha: 'abc1234', start_time: 1000, uptime: '2h' },
      services: [
        {
          name: 'sharedllm_gateway',
          status: 'running',
          image: 'ghcr.io/jmiahman1/sharedllm-gateway:latest',
          uptime: '2h',
          restart_count: 0,
        },
        {
          name: 'sharedllm_osrm_foot',
          status: 'running',
          image: 'ghcr.io/jmiahman1/sharedllm-osrm-foot:latest',
          uptime: '2h',
          restart_count: 0,
        },
      ],
    });
    mockApi.checkAllUpdates.mockResolvedValue({
      checked: 2,
      updates_available: 2,
      services: [
        {
          service: 'gateway',
          container_name: 'sharedllm_gateway',
          image: 'ghcr.io/jmiahman1/sharedllm-gateway:latest',
          current_digest: 'sha256:old1',
          remote_digest: 'sha256:new1',
          has_update: true,
          status: 'running',
        },
        {
          service: 'osrm-foot',
          container_name: 'sharedllm_osrm_foot',
          image: 'ghcr.io/jmiahman1/sharedllm-osrm-foot:latest',
          current_digest: 'sha256:old2',
          remote_digest: 'sha256:new2',
          has_update: true,
          status: 'running',
        },
      ],
    });
    mockApi.pullAndRestart.mockResolvedValue({
      service: 'sharedllm_osrm_foot',
      status: 'SUCCESS',
      message: 'Container sharedllm_osrm_foot updated and restarted with new image',
      updated: true,
      current_image_id: 'sha256:old2',
      new_image_id: 'sha256:new2',
    });
  });

  it('matches hyphenated compose services and triggers pullAndRestart (not pull-only) on Update & Restart', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/admin/services']}>
          <Admin />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const badges = await screen.findAllByText(/Update Available/i);
    // Both sharedllm_gateway and hyphenated osrm-foot <-> sharedllm_osrm_foot match!
    expect(badges).toHaveLength(2);

    const updateButtons = screen.getAllByRole('button', { name: /Update & Restart/i });
    expect(updateButtons).toHaveLength(2);

    await userEvent.setup().click(updateButtons[1]);
    await waitFor(() => {
      expect(mockApi.pullAndRestart).toHaveBeenCalledWith('sharedllm_osrm_foot');
    });
    expect(mockApi.pullServiceImage).not.toHaveBeenCalled();
  });
});

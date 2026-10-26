import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Admin from './Admin';
import { renderWithProviders } from '../test/render';
import { api } from '../services/api';
import { http, HttpResponse } from 'msw';
import { server } from '../test/setup';
import { confirmDialog } from '../components/ui/confirmDialog';

// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

// The Users & Devices tab has two entity dropdowns (device assignment and
// entity protection), so the mock honours `testId`; `getByTestId` throws on
// duplicates rather than quietly picking the first one.
vi.mock('../components/ui/EntitySearchDropdown', () => ({
  default: ({
    value,
    onChange,
    placeholder,
    testId,
  }: {
    value: string;
    onChange: (v: string) => void;
    placeholder?: string;
    testId?: string;
  }) => (
    <input
      data-testid={testId ?? 'entity-search'}
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

vi.stubGlobal('confirm', vi.fn(() => true));

describe('Admin page', () => {
  it('api.discoverUsers returns discovered users via MSW', async () => {
    const result = await api.discoverUsers();
    expect(result.users).toHaveLength(2);
    expect(result.users[0].username).toBe('jeremiah');
  });

  it('renders discovery users when no filter is applied', async () => {
    renderWithProviders(<Admin />);
    
    await screen.findByText('User Management');
    await screen.findByText('Discovery Import');
    await screen.findByText('Shared/Default User');
    
    await waitFor(() => {
      expect(screen.getByText('Jeremiah')).toBeInTheDocument();
    }, { timeout: 5000 });
    
    expect(screen.getByText('Jeremiah')).toBeInTheDocument();
    expect(screen.getByText('Michele')).toBeInTheDocument();
  });
  it('renders tab navigation and default users tab', async () => {
    renderWithProviders(<Admin />);

    expect(await screen.findByText('Users & Devices')).toBeInTheDocument();
    expect(screen.getByText('Raven Ops')).toBeInTheDocument();
    expect(screen.getByText('LLM & Settings')).toBeInTheDocument();
    expect(screen.getByText('Database & Audit')).toBeInTheDocument();
    expect(await screen.findByText('User Management')).toBeInTheDocument();
    expect(await screen.findByText('Shared/Default User')).toBeInTheDocument();
  });

  it('creates a user from the modal and shows it in the list', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByText('Create User'));
    fireEvent.change(screen.getByLabelText(/^Username$/i), { target: { value: 'jeremiah' } });
    fireEvent.change(screen.getByLabelText(/^Display Name$/i), { target: { value: 'Jeremiah Summers' } });
    fireEvent.click(screen.getByText('Save User'));

    expect(await screen.findByText('Jeremiah Summers')).toBeInTheDocument();
  });

  it('imports a discovered user into the user list', async () => {
    renderWithProviders(<Admin />);

    await screen.findByText('Discovery Import');
    
    const jeremiahElement = await screen.findByText('Jeremiah');
    expect(jeremiahElement).toBeInTheDocument();
    
    const importButtons = await screen.findAllByText('Import');
    expect(importButtons.length).toBeGreaterThan(0);
    fireEvent.click(importButtons[0]);

    await waitFor(() => expect(screen.getAllByText('Jeremiah').length).toBeGreaterThan(0));
  });

  it('saves a new device assignment and a new setting', async () => {
    renderWithProviders(<Admin />);

    fireEvent.change(await screen.findByTestId('entity-search'), {
      target: { value: 'media_player.kitchen_echo' },
    });
    fireEvent.change(screen.getByLabelText('Device User'), {
      target: { value: 'default' },
    });
    fireEvent.click(screen.getByText('Add person'));

    expect(await screen.findByText('media_player.kitchen_echo')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Database & Audit'));
    fireEvent.change(await screen.findByPlaceholderText('new_setting_key'), { target: { value: 'feature_flag' } });
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: 'enabled' } });
    fireEvent.click(screen.getByText('Add'));

    expect(await screen.findByDisplayValue('enabled')).toBeInTheDocument();
  });

  it('lets an admin edit a user\'s shared service access from the user row', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByRole('button', { name: 'Shared service access for default' }));

    const dialog = await screen.findByTestId('shared-credentials');
    expect(dialog).toHaveTextContent(/own credentials always win/i);

    const nextcloudToggle = screen.getByRole('checkbox', { name: 'Use shared Nextcloud credentials' });
    expect(nextcloudToggle).not.toBeChecked();
    fireEvent.click(nextcloudToggle);

    await waitFor(() => {
      expect(
        screen.getByRole('checkbox', { name: 'Use shared Nextcloud credentials' }),
      ).toBeChecked();
    });
  });

  it('shows Raven Ops panel when tab is clicked', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByText('Raven Ops'));
    expect(await screen.findByText('Raven Mission Control')).toBeInTheDocument();
    expect(screen.getByText('Proposed fixes')).toBeInTheDocument();
    expect(screen.getByText('Running now')).toBeInTheDocument();
  });

  it('shows LLM settings when tab is clicked', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(screen.getByText('LLM & Settings'));
    expect(await screen.findByText('Local Model Mapping')).toBeInTheDocument();
  });

  it('shows database insights when tab is clicked', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(screen.getByText('Database & Audit'));
    expect(await screen.findByText('Advanced Database Insights')).toBeInTheDocument();
    expect(screen.getByText('Audit Trail')).toBeInTheDocument();
  });

  it('services: an unreachable registry is not reported as "0 updates", and restart asks first', async () => {
    const restarts: string[] = [];
    server.use(
      http.get('/api/admin/services/health', () => HttpResponse.json({
        total_services: 1, running: 1, stopped: 0, unhealthy: 0,
        control_plane: { status: 'running', git_sha: 'abc1234', start_time: 0, uptime: '1h' },
        services: [{ name: 'sharedllm_geo', status: 'running', image: 'ghcr.io/o/sharedllm-geo:abc1234', uptime: '1h' }],
      })),
      http.get('/api/admin/services/updates', () => HttpResponse.json({
        checked: 1, updates_available: 0, check_error: 'registry_unreachable',
        services: [{ service: 'geo', has_update: false, check_error: 'registry_unreachable' }],
      })),
      http.post('/api/admin/services/:name/restart', ({ params }) => {
        restarts.push(String(params.name));
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
    const confirm = vi.fn(() => false);
    vi.stubGlobal('confirm', confirm);
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByText('System Services'));
    expect(await screen.findByText('geo')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Check Failed')).toBeInTheDocument());
    expect(screen.getByText('No data')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^restart$/i }));
    expect(confirm).toHaveBeenCalled();
    expect(restarts).toEqual([]);
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('deleting a user asks first', async () => {
    const deleted: string[] = [];
    server.use(
      http.get('/api/users', () => HttpResponse.json([
        { id: 2, username: 'leo', full_name: 'Leo', is_admin: false, is_system_default: false },
      ])),
      http.delete('/api/users/:name', ({ params }) => {
        deleted.push(String(params.name));
        return HttpResponse.json({ status: 'ok' });
      }),
    );
    confirmMock.mockResolvedValue(false);
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByRole('button', { name: 'Delete leo' }));
    expect(confirmMock).toHaveBeenCalled();
    expect(deleted).toEqual([]);
    confirmMock.mockResolvedValue(true);
  });
});

import { screen, waitFor } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import Dashboard from '../pages/Dashboard';
import { renderWithProviders } from './render';

describe('Dashboard decluttering', () => {
  it('no longer renders the cluttered System Settings list on the main dashboard', async () => {
    renderWithProviders(<Dashboard />);

    // Dashboard should still render its header.
    await waitFor(() => {
      expect(screen.getByText('Jarvis Dashboard')).toBeInTheDocument();
    });

    // The long-list settings panel must be migrated away from the dashboard.
    expect(screen.queryByText('System Settings')).not.toBeInTheDocument();
  });
});

describe('Dashboard recent problems', () => {
  it('asks the log for warnings and errors only and lists them for an admin', async () => {
    const { server } = await import('./setup');
    const { http, HttpResponse } = await import('msw');
    let asked = '';
    server.use(
      http.get('/api/admin/logs', ({ request }) => {
        asked = new URL(request.url).searchParams.get('level') ?? '';
        return HttpResponse.json([
          { timestamp: '2026-10-15 16:40:07', service: 'gateway', level: 'WARNING', message: 'RESPONSE GET /api/users/location/all -> 401' },
        ]);
      }),
    );
    renderWithProviders(<Dashboard />);

    expect(await screen.findByText('Recent Problems')).toBeInTheDocument();
    expect(await screen.findByText('RESPONSE GET /api/users/location/all -> 401')).toBeInTheDocument();
    expect(asked).toBe('warning,error');
  });

  it('confirms before clearing the whole system log', async () => {
    const { server } = await import('./setup');
    const { http, HttpResponse } = await import('msw');
    let deleted = false;
    server.use(
      http.get('/api/admin/logs', () => HttpResponse.json([
        { timestamp: '2026-10-15 16:40:07', service: 'gateway', level: 'ERROR', message: 'RESPONSE GET /x -> 502' },
      ])),
      http.delete('/api/admin/logs', () => { deleted = true; return HttpResponse.json({ status: 'success' }); }),
    );
    const { default: userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    renderWithProviders(<Dashboard />);

    await user.click(await screen.findByRole('button', { name: 'Clear' }));
    expect(deleted).toBe(false);
    await user.click(screen.getByRole('button', { name: /clear whole log/i }));
    await waitFor(() => expect(deleted).toBe(true));
  });
});

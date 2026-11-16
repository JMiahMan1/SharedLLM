import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import RavenOpsPanel from '../components/settings/RavenOpsPanel';
import { renderWithProviders } from './render';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

describe('RavenOpsPanel', () => {
  it('starts a mission through the real missions endpoint', async () => {
    let posted: unknown = null;
    server.use(
      http.post('/api/raven/missions', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({
          status: 'SUCCESS',
          mission: { id: 42, mission_type: 'user_task', status: 'queued', progress: 0, created_at: '2026-10-15T00:00:00Z', proposed_mission: 'x' },
        });
      }),
      // The live trace opens on success; keep it quiet.
      http.get('/api/raven/missions/:id', () => HttpResponse.json({ id: 42, status: 'queued', output_log: '[]' })),
      http.get('/api/raven/missions/:id/logs', () => HttpResponse.json({ logs: [] })),
    );
    const user = userEvent.setup();
    renderWithProviders(<RavenOpsPanel />);

    await user.type(await screen.findByLabelText('Mission brief'), 'Check why the geo service restarts');
    await user.click(screen.getByRole('button', { name: /start mission/i }));

    await waitFor(() => expect(posted).toMatchObject({ query: 'Check why the geo service restarts' }));
  });

  it('shows no fake controls (search test, load bar, cleanup interval, compliance claim)', async () => {
    renderWithProviders(<RavenOpsPanel />);
    await screen.findByText('Raven Mission Control');

    expect(screen.queryByText(/SearXNG/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Inference Load/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cleanup Interval/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Sentinel Compliance/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Manual Correction/i)).not.toBeInTheDocument();
  });

  it('offers the featured voices by name, ahead of the full list', async () => {
    server.use(
      http.get('/api/admin/raven/tts/voices', () => HttpResponse.json({
        status: 'SUCCESS',
        voices: ['af_heart', 'bm_george', 'piper:en_US-test-medium'],
        featured: [
          { id: 'bm_fable,bm_lewis,bm_george', name: 'Parable Party announcer' },
          { id: 'piper:en_US-test-medium', name: 'en_US-test-medium (Piper)' },
        ],
      })),
    );
    renderWithProviders(<RavenOpsPanel />);
    const announcer = await screen.findByRole('option', { name: 'Parable Party announcer' });
    expect(announcer).toHaveValue('bm_fable,bm_lewis,bm_george');
    expect(screen.getByRole('option', { name: 'en_US-test-medium (Piper)' })).toHaveValue('piper:en_US-test-medium');
    // A featured voice is listed once, under Featured, not again under All voices.
    expect(screen.queryByRole('option', { name: 'piper:en_US-test-medium' })).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'bm_george' })).toBeInTheDocument();
  });
});

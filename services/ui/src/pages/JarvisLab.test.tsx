import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import JarvisLab from './JarvisLab';
import RavenAuditLog from '../components/settings/RavenAuditLog';
import { renderWithProviders } from '../test/render';
import { server } from '../test/setup';

const mission = (over: Record<string, unknown>) => ({
  id: 7, mission_type: 'user_task', priority: 1, status: 'completed', progress: 100,
  created_at: '2026-10-15T10:00:00Z', proposed_mission: 'Tidy the workspace', ...over,
});

// The list row carries no log; the full mission (by id) does.
const LOG = JSON.stringify([
  { type: 'action', data: 'WorkspaceShellRequest', timestamp: 1792100000 },
  { type: 'action_payload', data: '{"cmd":"ls"}', timestamp: 1792100001 },
  { type: 'result_success', data: 'listed 3 files', timestamp: 1792100002 },
]);

describe('JarvisLab page', () => {
  it('runs the smoke test and shows raw output', async () => {
    renderWithProviders(<JarvisLab />);

    fireEvent.click(screen.getByText('Tests'));
    fireEvent.click(await screen.findByText('Run Smoke Test'));

    expect(await screen.findByText(/PASS: health/)).toBeInTheDocument();
  });

  it('filters by the statuses missions really have, including queued', async () => {
    server.use(http.get('/api/raven/missions', () => HttpResponse.json([
      mission({ id: 1, status: 'queued', proposed_mission: 'Waiting job' }),
      mission({ id: 2, status: 'completed', proposed_mission: 'Done job' }),
    ])));
    renderWithProviders(<JarvisLab />);

    expect(await screen.findByText('Done job')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'queued' }));
    expect(screen.getByText('Waiting job')).toBeInTheDocument();
    expect(screen.queryByText('Done job')).not.toBeInTheDocument();
  });

  it('opens a mission and shows its log loaded by id', async () => {
    server.use(
      http.get('/api/raven/missions', () => HttpResponse.json([mission({})])),
      http.get('/api/raven/missions/:id', () => HttpResponse.json(mission({ output_log: LOG, result: 'All tidy' }))),
    );
    renderWithProviders(<JarvisLab />);

    fireEvent.click(await screen.findByRole('button', { name: /inspect & refine/i }));
    const dialog = await screen.findByRole('dialog', { name: 'Mission #7' });
    expect(await within(dialog).findByText('listed 3 files')).toBeInTheDocument();
    expect(within(dialog).getByText('All tidy')).toBeInTheDocument();
    // The summary timeline leaves tool payloads out.
    expect(within(dialog).queryByText('{"cmd":"ls"}')).not.toBeInTheDocument();
  });
});

describe('RavenAuditLog', () => {
  it('shows the selected mission’s full log, fetched by id', async () => {
    server.use(
      http.get('/api/admin/raven/queue', () => HttpResponse.json([mission({ output_log: null })])),
      http.get('/api/raven/missions/:id', () => HttpResponse.json(mission({ output_log: LOG }))),
    );
    renderWithProviders(<RavenAuditLog isOpen onClose={() => {}} />);

    fireEvent.click(await screen.findByText(/#7/));
    await waitFor(() => expect(screen.getByText('listed 3 files')).toBeInTheDocument());
    expect(screen.getByText('{"cmd":"ls"}')).toBeInTheDocument();
  });
});

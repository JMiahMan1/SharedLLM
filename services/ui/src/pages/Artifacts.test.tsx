import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import Artifacts from './Artifacts';
import { renderWithProviders } from '../test/render';
import { server } from '../test/setup';
import type { Artifact } from '../types/api';

const usage = {
  user: 'default',
  used: 400 * 1024 * 1024,
  quota: 500 * 1024 * 1024,
  free: 20 * 1024 ** 3,
  count: 1,
  by_kind: { tts_audio: 400 * 1024 * 1024 },
  warn: true,
};

const artifact: Artifact = {
  id: 'art-1',
  owner_user: 'default',
  workspace_id: 'code',
  kind: 'tts_audio',
  name: 'morning-brief.mp3',
  mime_type: 'audio/mpeg',
  size: 400 * 1024 * 1024,
  storage_backend: 'local',
  metadata: { voice: 'Jane' },
  created_at: '2026-10-18T12:00:00Z',
  updated_at: '2026-10-18T12:00:00Z',
  deleted_at: null,
  is_attached: true,
  download_url: '/api/artifacts/art-1/stream?token=x',
};

describe('Artifacts page', () => {
  it('shows the storage warning and lists generated files', async () => {
    server.use(
      http.get('/api/storage/usage', () => HttpResponse.json(usage)),
      http.get('/api/artifacts', () => HttpResponse.json({ artifacts: [artifact], total: 1, limit: 50, offset: 0 })),
    );
    renderWithProviders(<Artifacts />);

    expect(await screen.findByText('morning-brief.mp3')).toBeInTheDocument();
    expect(screen.getByText('400.0 MB')).toBeInTheDocument();
    expect(screen.getByText('500.0 MB')).toBeInTheDocument();
    expect(screen.getByText(/80% of your storage/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview morning-brief.mp3' })).toBeInTheDocument();
  });

  it('moves an artifact to Recently deleted and restores it from there', async () => {
    let listing: Artifact[] = [artifact];
    server.use(
      http.get('/api/storage/usage', () => HttpResponse.json({ ...usage, warn: false })),
      http.get('/api/artifacts', () => HttpResponse.json({ artifacts: listing, total: listing.length, limit: 50, offset: 0 })),
      http.delete('/api/artifacts/art-1', () => {
        listing = [{ ...artifact, deleted_at: '2026-10-19T12:00:00Z' }];
        return HttpResponse.json({ ...listing[0], hard: false });
      }),
      http.post('/api/artifacts/art-1/restore', () => {
        listing = [artifact];
        return HttpResponse.json(artifact);
      }),
    );
    renderWithProviders(<Artifacts />);

    fireEvent.click(await screen.findByRole('button', { name: 'Delete morning-brief.mp3' }));
    expect(await screen.findByRole('button', { name: 'Restore' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Restore' })).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Delete morning-brief.mp3' })).toBeInTheDocument();
  });

  it('requires the exact phrase before a permanent delete', async () => {
    let deleted: unknown = null;
    server.use(
      http.get('/api/storage/usage', () => HttpResponse.json({ ...usage, warn: false })),
      http.get('/api/artifacts', () => HttpResponse.json({
        artifacts: [{ ...artifact, deleted_at: '2026-10-19T12:00:00Z' }],
        total: 1,
        limit: 50,
        offset: 0,
      })),
      http.delete('/api/artifacts/art-1', async ({ request }) => {
        deleted = await request.json();
        return HttpResponse.json({ ...artifact, hard: true });
      }),
    );
    renderWithProviders(<Artifacts />);

    fireEvent.click(await screen.findByRole('button', { name: 'Delete morning-brief.mp3 permanently' }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Delete forever' });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Confirmation phrase'), {
      target: { value: 'delete morning-brief.mp3' },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(deleted).toEqual({ confirm_phrase: 'delete morning-brief.mp3' }));
  });
});

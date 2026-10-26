import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Workspaces from './Workspaces';
import { renderWithProviders } from '../test/render';
import { server } from '../test/setup';
import { confirmDialog } from '../components/ui/confirmDialog';

// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

const listOf = (...workspaces: Record<string, unknown>[]) =>
  http.get('/api/workspaces', () => HttpResponse.json({ status: 'SUCCESS', workspaces }));

const gitWs = {
  id: 'code', display_name: 'Code', local_path: 'code', scope: 'user', sync_mode: 'git',
  auto_pull_enabled: true, git_remote: 'origin', default_branch: 'main',
  // Values the runtime computes; an edit must not send them back.
  capabilities: ['read', 'write'], resolved_path: '/workspaces/code', available: true,
};
const nextcloudWs = {
  id: 'sermons', display_name: 'Sermons', local_path: 'sermons', scope: 'user', sync_mode: 'nextcloud',
  nextcloud_path: '/Documents/Sermons', auto_pull_enabled: true, capabilities: [],
  last_sync_at: '2026-10-15T16:41:07-07:00', last_sync_status: 'ok',
};

afterEach(() => vi.unstubAllGlobals());

describe('Workspaces page', () => {
  it('shows copy-paste webhook setup values for a workspace that follows its remote', async () => {
    renderWithProviders(<Workspaces />);

    fireEvent.click(await screen.findByText('Add Workspace'));
    fireEvent.change(screen.getByPlaceholderText('project-id'), { target: { value: 'demo-workspace' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Git (follows remote)' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Toggle automated sync' }));

    expect(await screen.findByText('Webhook Setup')).toBeInTheDocument();
    expect(screen.getByText('GitHub')).toBeInTheDocument();
    expect(screen.getByText('GitLab')).toBeInTheDocument();
    // The token travels as the hook's secret, not in the URL.
    expect(screen.getByText(/\/api\/webhook\/git-pull\/demo-workspace$/)).toBeInTheDocument();
    // A fresh token comes from the CSPRNG: 32 bytes of hex.
    const token = (screen.getByPlaceholderText('Secret for GitHub or GitLab') as HTMLInputElement).value;
    expect(token).toMatch(/^[0-9a-f]{64}$/);
  });

  it('says a push webhook is ignored while local git is authoritative', async () => {
    renderWithProviders(<Workspaces />);
    fireEvent.click(await screen.findByText('Add Workspace'));
    fireEvent.click(screen.getByRole('switch', { name: 'Toggle automated sync' }));
    expect(screen.getByText(/Ignored while local git is authoritative/)).toBeInTheDocument();
    expect(screen.queryByText('Webhook Setup')).not.toBeInTheDocument();
  });

  it('offers Sync Now, not a git pull, for a Nextcloud workspace', async () => {
    let synced: unknown = null;
    server.use(
      listOf(nextcloudWs),
      http.post('/api/workspaces/sync', async ({ request }) => {
        synced = await request.json();
        return HttpResponse.json({ status: 'SUCCESS', workspace_id: 'sermons', result: { conflicts: [] } });
      }),
    );
    renderWithProviders(<Workspaces />);

    fireEvent.click(await screen.findByRole('button', { name: 'Sync Now' }));
    expect(screen.queryByText('Pull Now')).not.toBeInTheDocument();
    expect(screen.queryByText('Push Webhook')).not.toBeInTheDocument();
    expect(screen.getByText(/^Synced /)).toBeInTheDocument();
    await waitFor(() => expect(synced).toEqual({ workspace_id: 'sermons' }));
  });

  it('saves only the fields the form edits', async () => {
    let patched: Record<string, unknown> | null = null;
    server.use(
      listOf(gitWs),
      http.patch('/api/workspaces/code', async ({ request }) => {
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
    renderWithProviders(<Workspaces />);

    fireEvent.click(await screen.findByRole('button', { name: 'Configure Code' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByText('Save Workspace'));

    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toMatchObject({ display_name: 'Code', sync_mode: 'git', auto_pull_enabled: true });
    for (const computed of ['capabilities', 'resolved_path', 'available', 'id', 'webhook_token']) {
      expect(patched).not.toHaveProperty(computed);
    }
  });

  it('lets a member open their workspace but not manage it', async () => {
    server.use(
      http.get('/api/users/me', () => HttpResponse.json({ id: 2, username: 'kid', is_admin: false, role: 'user' })),
      listOf({ ...gitWs, owner_user: 'kid' }),
    );
    renderWithProviders(<Workspaces />);

    expect(await screen.findByRole('button', { name: 'Open Code' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Add Workspace')).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Delete Code' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Configure Code' })).not.toBeInTheDocument();
  });

  it('asks what to do with artifacts before deleting a workspace', async () => {
    const deletes: unknown[] = [];
    confirmMock.mockResolvedValue(true);
    server.use(
      listOf(gitWs),
      http.delete('/api/workspaces/code', async ({ request }) => {
        const raw = await request.text();
        deletes.push(raw ? JSON.parse(raw) : null);
        if (deletes.length === 1) {
          return HttpResponse.json(
            {
              status: 'ERROR',
              detail: 'This workspace has 2 artifacts.',
              artifact_count: 2,
              needs_confirmation: true,
            },
            { status: 409 },
          );
        }
        return HttpResponse.json({ status: 'SUCCESS', message: 'Workspace code deleted' });
      }),
    );
    renderWithProviders(<Workspaces />);

    fireEvent.click(await screen.findByRole('button', { name: 'Delete Code' }));
    const dialog = await screen.findByRole('dialog', { name: 'Workspace has saved artifacts' });
    expect(within(dialog).getByText(/has 2 saved artifacts/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByText('Delete workspace, keep artifacts in library'));
    await waitFor(() => expect(deletes).toHaveLength(2));
    expect(deletes[1]).toEqual({ delete_artifacts: false });
  });
});

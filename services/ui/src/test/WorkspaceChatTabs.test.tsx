import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import WorkspaceIDE from '../components/workspace/WorkspaceIDE';
import type { Workspace, WorkspaceChatMessage } from '../types/api';
import { renderWithProviders } from './render';
import { server } from './setup';

const workspace: Workspace = {
  id: 'ws-1',
  display_name: 'Test Workspace',
  local_path: '/tmp/ws-1',
  sync_mode: 'git',
  scope: 'isolated',
  capabilities: [],
  auto_pull_enabled: false,
};

let messages: WorkspaceChatMessage[] = [];
let turns: Array<{ message: string; mode: string }> = [];

const ndjson = (events: unknown[]) =>
  new HttpResponse(events.map((e) => JSON.stringify(e)).join('\n') + '\n', { headers: { 'Content-Type': 'application/x-ndjson' } });

describe('workspace chat tabs', () => {
  beforeEach(() => {
    messages = [];
    turns = [];
    server.use(
      http.get('/api/workspaces/:id/raven/missions', () => HttpResponse.json([])),
      http.get('/api/workspaces/:id/chats', () => HttpResponse.json({ chats: [] })),
      http.post('/api/workspaces/:id/chats', () =>
        HttpResponse.json({ id: 'c1', workspace_id: 'ws-1', title: 'New chat', created_at: '', updated_at: '', message_count: 0 }),
      ),
      http.get('/api/workspaces/:id/chats/:chatId', () =>
        HttpResponse.json({ id: 'c1', workspace_id: 'ws-1', title: messages.length ? 'Find the mkv' : 'New chat', created_at: '', updated_at: '', messages }),
      ),
      http.post('/api/workspaces/:id/chats/:chatId/turn', async ({ request }) => {
        const body = (await request.json()) as { message: string; mode: string };
        turns.push(body);
        const reply: WorkspaceChatMessage = {
          role: 'assistant',
          parts: [
            { type: 'reasoning', text: 'Search for it.', step: 1 },
            { type: 'tool', id: 's1', name: 'WorkspaceSearchRequest', input: { query: 'mkv' }, output: '- service.mkv', status: 'done' },
            { type: 'text', text: `Answer to: ${body.message}` },
          ],
          meta: { resolved_mode: 'single_task', model: 'assistant-model', status: 'done' },
        };
        messages.push({ role: 'user', parts: [{ type: 'text', text: body.message }] }, reply);
        return ndjson([
          { type: 'start', requested_mode: body.mode, resolved_mode: 'single_task', reason: 'a task' },
          { type: 'step', n: 1 },
          { type: 'thinking', text: 'Search for it.', step: 1 },
          { type: 'tool_call', id: 's1', name: 'WorkspaceSearchRequest', input: { query: 'mkv' } },
          { type: 'tool_result', id: 's1', output: '- service.mkv' },
          { type: 'done', message: reply },
        ]);
      }),
    );
  });

  it('opens a chat as a tab, shows the steps, and continues when answered', async () => {
    const user = userEvent.setup();
    renderWithProviders(<WorkspaceIDE workspace={workspace} onClose={() => {}} />);
    await user.click(await screen.findByRole('button', { name: /^chat$/i }));

    const sidebarComposer = (await screen.findAllByTestId('chat-composer'))[0];
    await user.type(within(sidebarComposer).getByLabelText('Workspace prompt'), 'Find the mkv{Enter}');

    const tab = await screen.findByTestId('workspace-chat-tab');
    await waitFor(() => expect(within(tab).getByTestId('chat-transcript')).toHaveTextContent('Answer to: Find the mkv'));
    expect(within(tab).getByTestId('tool-part')).toHaveTextContent(/workspace search/i);
    expect(turns).toEqual([{ message: 'Find the mkv', mode: 'auto' }]);

    // Answering continues the same chat, in the tab.
    await user.type(within(tab).getByLabelText('Workspace prompt'), 'Now transcribe it{Enter}');
    await waitFor(() => expect(within(tab).getByTestId('chat-transcript')).toHaveTextContent('Answer to: Now transcribe it'));
    expect(turns.map((t) => t.message)).toEqual(['Find the mkv', 'Now transcribe it']);
  });
});

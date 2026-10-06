import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import WorkspaceIDE from '../components/workspace/WorkspaceIDE';
import type { Workspace } from '../types/api';
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

interface AskBody {
  query: string;
  mode: string;
}

let asks: AskBody[] = [];

const seedGateway = () => {
  server.use(
    http.get('/api/workspaces/:id/raven/missions', () => HttpResponse.json([])),
    http.post('/api/workspaces/:id/ask', async ({ request, params }) => {
      const body = (await request.json()) as AskBody;
      asks.push(body);
      if (body.mode === 'raven') {
        return HttpResponse.json({
          status: 'SUCCESS',
          requested_mode: 'raven',
          resolved_mode: 'raven',
          reason: 'chosen explicitly',
          context_chars: 1200,
          mission_id: 42,
          mission: { id: 42 },
        });
      }
      expect(params.id).toBe('ws-1');
      return HttpResponse.json({
        status: 'SUCCESS',
        requested_mode: body.mode,
        resolved_mode: body.mode === 'auto' ? 'librarian' : body.mode,
        reason: body.mode === 'auto' ? 'question shaped' : 'chosen explicitly',
        model: 'librarian-model',
        context_chars: 1200,
        answer: `Answer to: ${body.query}`,
      });
    }),
  );
};

const sendButton = (mode: string) => screen.getByRole('button', { name: `Send as ${mode}` });

const openChat = async () => {
  renderWithProviders(<WorkspaceIDE workspace={workspace} onClose={() => {}} />);
  fireEvent.click(await screen.findByTitle('Chat'));
  return screen.findByRole('group', { name: 'Response mode' });
};

describe('WorkspaceIDE composer modes', () => {
  beforeEach(() => {
    asks = [];
    seedGateway();
  });

  it('offers all four modes with auto selected by default', async () => {
    await openChat();
    for (const label of ['Auto', 'Ask', 'Task', 'Raven']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('defaults to auto and reports the mode the gateway resolved', async () => {
    await openChat();
    fireEvent.change(screen.getByRole('textbox', { name: 'Workspace prompt' }), { target: { value: 'What did Macduff say?' } });
    fireEvent.click(sendButton('Auto'));

    await screen.findByText('Answer to: What did Macduff say?');
    expect(asks).toHaveLength(1);
    expect(asks[0].mode).toBe('auto');
    expect(screen.getByText('question shaped')).toBeInTheDocument();
    expect(screen.getByText(/1200 chars retrieved/)).toBeInTheDocument();
    expect(screen.getByText(/librarian-model/)).toBeInTheDocument();
  });

  it('sends the librarian mode when Ask is chosen, and names it in the chip', async () => {
    await openChat();
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    expect(screen.getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.change(screen.getByRole('textbox', { name: 'Workspace prompt' }), { target: { value: 'quote the sermon' } });
    fireEvent.click(sendButton('Ask'));

    await screen.findByText('Answer to: quote the sermon');
    expect(asks[0].mode).toBe('librarian');
    expect(screen.getByText('chosen explicitly')).toBeInTheDocument();
  });

  it('sends single_task when Task is chosen', async () => {
    await openChat();
    fireEvent.click(screen.getByRole('button', { name: 'Task' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Workspace prompt' }), { target: { value: 'rename this variable' } });
    fireEvent.click(sendButton('Task'));

    await screen.findByText('Answer to: rename this variable');
    expect(asks[0].mode).toBe('single_task');
    expect(screen.getByRole('button', { name: /Hand this to Raven/ })).toBeInTheDocument();
  });

  it('dispatches a mission when Raven is chosen and offers no escalation', async () => {
    await openChat();
    fireEvent.click(screen.getByRole('button', { name: 'Raven' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Workspace prompt' }), { target: { value: 'fix the failing test' } });
    fireEvent.click(sendButton('Raven'));

    await waitFor(() => expect(asks).toHaveLength(1));
    expect(asks[0].mode).toBe('raven');
    expect(screen.queryByRole('button', { name: /Hand this to Raven/ })).not.toBeInTheDocument();
  });

  it('escalates a finished answer into a Raven brief carrying that answer', async () => {
    await openChat();
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Workspace prompt' }), { target: { value: 'what is lectio divina?' } });
    fireEvent.click(sendButton('Ask'));

    await screen.findByText('Answer to: what is lectio divina?');
    fireEvent.click(screen.getByRole('button', { name: /Hand this to Raven/ }));

    await waitFor(() => expect(asks).toHaveLength(2));
    expect(asks[1].mode).toBe('raven');
    expect(asks[1].query).toContain('what is lectio divina?');
    expect(asks[1].query).toContain('Answer to: what is lectio divina?');
    expect(asks[1].query).toContain('1200 characters of retrieved context');
  });

  it('reports a failure instead of showing a stale answer', async () => {
    await openChat();
    server.use(
      http.post('/api/workspaces/:id/ask', () =>
        HttpResponse.json({ detail: 'A query is required.' }, { status: 400 }),
      ),
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Workspace prompt' }), { target: { value: 'anything' } });
    fireEvent.click(sendButton('Auto'));

    await waitFor(() => expect(screen.queryByRole('button', { name: /Hand this to Raven/ })).toBeNull());
  });
});
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import ChoresCard from './ChoresCard';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const auth = vi.hoisted(() => ({ user: { username: 'mom', is_admin: false, family_role: 'mother' } as Record<string, unknown> | null }));
vi.mock('../../context/AuthContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../context/AuthContext')>()),
  useAuth: () => auth,
}));

let asked: string[] = [];
let added: unknown[] = [];
let removed: Array<{ id: string; applyTo: string | null }> = [];
let listing: Record<string, unknown> = {};

beforeEach(() => {
  auth.user = { username: 'mom', is_admin: false, family_role: 'mother' };
  asked = [];
  added = [];
  removed = [];
  listing = {
    status: 'SUCCESS',
    chores: [
      { id: '50-2026-11-03', title: 'Feed the chickens', completed: false, recurrence: 'RRULE:FREQ=DAILY', start_time: '07:00', assignees: ['Noah'] },
      { id: '51', title: 'Trash', completed: true, assignees: ['Noah'] },
    ],
  };
  server.use(
    http.get('*/api/integrations/skylight/chores', ({ request }) => {
      asked.push(new URL(request.url).search);
      return HttpResponse.json(listing);
    }),
    http.post('*/api/integrations/skylight/chores', async ({ request }) => {
      added.push(await request.json());
      return HttpResponse.json({ status: 'SUCCESS' });
    }),
    http.delete('*/api/integrations/skylight/chores/:id', ({ params, request }) => {
      removed.push({ id: String(params.id), applyTo: new URL(request.url).searchParams.get('apply_to') });
      return HttpResponse.json({ status: 'SUCCESS' });
    }),
  );
});

describe('ChoresCard', () => {
  it("shows the child's chores for today", async () => {
    renderWithProviders(<ChoresCard username="noah" name="Noah" />);
    expect(await screen.findByText('Feed the chickens')).toBeInTheDocument();
    expect(screen.getByText('1 of 2 left today')).toBeInTheDocument();
    expect(screen.getByText('7 am')).toBeInTheDocument();
    expect(asked[0]).toContain('scope=noah');
  });

  it('adds a repeating chore for the child', async () => {
    renderWithProviders(<ChoresCard username="noah" name="Noah" />);
    await screen.findByText('Feed the chickens');
    fireEvent.change(screen.getByLabelText('Chore name'), { target: { value: ' Hay the horses ' } });
    fireEvent.change(screen.getByLabelText('How often'), { target: { value: 'weekdays' } });
    fireEvent.change(screen.getByLabelText('Time (optional)'), { target: { value: '16:30' } });
    fireEvent.change(screen.getByLabelText('Stars (optional)'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /Add chore/ }));
    await waitFor(() => expect(added).toHaveLength(1));
    expect(added[0]).toEqual({ title: 'Hay the horses', assignees: ['noah'], time: '16:30', repeat: 'weekdays', stars: 3 });
    expect(screen.getByLabelText('Chore name')).toHaveValue('');
  });

  it('removes a one-off at once and asks about a repeating chore', async () => {
    renderWithProviders(<ChoresCard username="noah" name="Noah" />);
    await screen.findByText('Trash');
    fireEvent.click(screen.getByRole('button', { name: 'Remove Trash' }));
    await waitFor(() => expect(removed).toEqual([{ id: '51', applyTo: null }]));

    fireEvent.click(screen.getByRole('button', { name: 'Remove Feed the chickens' }));
    expect(await screen.findByTestId('chore-remove-prompt')).toHaveTextContent('repeats');
    fireEvent.click(screen.getByRole('button', { name: 'Every day' }));
    await waitFor(() => expect(removed[1]).toEqual({ id: '50-2026-11-03', applyTo: 'all' }));
  });

  it('is not shown when Skylight is not set up', async () => {
    listing = { status: 'FAILURE', message: 'Skylight not configured' };
    const { container } = renderWithProviders(<ChoresCard username="noah" name="Noah" />);
    await waitFor(() => expect(asked).toHaveLength(1));
    await waitFor(() => expect(container.querySelector('[data-testid="guardian-chores"]')).toBeNull());
  });

  it('is not shown to someone who does not run the chore chart', async () => {
    auth.user = { username: 'gran', is_admin: false, family_role: 'other' };
    const { container } = renderWithProviders(<ChoresCard username="noah" name="Noah" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector('[data-testid="guardian-chores"]')).toBeNull();
    expect(asked).toEqual([]);
  });
});

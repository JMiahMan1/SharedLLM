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
let checks: Array<Record<string, unknown>> = [];
let decided: Array<{ id: string; body: unknown }> = [];

beforeEach(() => {
  auth.user = { username: 'mom', is_admin: false, family_role: 'mother' };
  asked = [];
  added = [];
  removed = [];
  checks = [];
  decided = [];
  listing = {
    status: 'SUCCESS',
    chores: [
      { id: '50-2026-11-03', title: 'Feed the chickens', completed: false, recurrence: 'RRULE:FREQ=DAILY', start_time: '07:00', assignees: ['Jordan'] },
      { id: '51', title: 'Trash', completed: true, assignees: ['Jordan'] },
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
    http.get('*/api/screen-time/chore-checks', () => HttpResponse.json({ checks })),
    http.post('*/api/screen-time/chore-checks/:id', async ({ params, request }) => {
      const body = await request.json();
      decided.push({ id: String(params.id), body });
      const check = checks.find((c) => String(c.id) === String(params.id));
      checks = checks.filter((c) => c !== check);
      return HttpResponse.json({ check: { ...check, status: (body as { approve: boolean }).approve ? 'approved' : 'declined' } });
    }),
    http.delete('*/api/integrations/skylight/chores/:id', ({ params, request }) => {
      removed.push({ id: String(params.id), applyTo: new URL(request.url).searchParams.get('apply_to') });
      return HttpResponse.json({ status: 'SUCCESS' });
    }),
  );
});

describe('ChoresCard', () => {
  it("shows the child's chores for today", async () => {
    renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
    expect(await screen.findByText('Feed the chickens')).toBeInTheDocument();
    expect(screen.getByText('1 of 2 left today')).toBeInTheDocument();
    expect(screen.getByText('7 am')).toBeInTheDocument();
    expect(asked[0]).toContain('scope=jordan');
  });

  it('adds a repeating chore for the child', async () => {
    renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
    await screen.findByText('Feed the chickens');
    fireEvent.change(screen.getByLabelText('Chore name'), { target: { value: ' Hay the horses ' } });
    fireEvent.change(screen.getByLabelText('How often'), { target: { value: 'weekdays' } });
    fireEvent.change(screen.getByLabelText('Time (optional)'), { target: { value: '16:30' } });
    fireEvent.change(screen.getByLabelText('Stars (optional)'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /Add chore/ }));
    await waitFor(() => expect(added).toHaveLength(1));
    expect(added[0]).toEqual({ title: 'Hay the horses', assignees: ['jordan'], time: '16:30', repeat: 'weekdays', stars: 3 });
    expect(screen.getByLabelText('Chore name')).toHaveValue('');
  });

  it('removes a one-off at once and asks about a repeating chore', async () => {
    renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
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
    const { container } = renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
    await waitFor(() => expect(asked).toHaveLength(1));
    await waitFor(() => expect(container.querySelector('[data-testid="guardian-chores"]')).toBeNull());
  });

  it('is not shown to someone who does not run the chore chart', async () => {
    auth.user = { username: 'gran', is_admin: false, family_role: 'other' };
    const { container } = renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector('[data-testid="guardian-chores"]')).toBeNull();
    expect(asked).toEqual([]);
  });

  it('asks a parent whether ticked-off chores were done right', async () => {
    const today = new Date();
    const day = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    checks = [
      { id: 7, username: 'jordan', day, chore_id: '51', title: 'Trash', status: 'pending' },
      { id: 8, username: 'jordan', day, chore_id: '52', title: 'Dishes', status: 'pending' },
      { id: 9, username: 'taylor', day, chore_id: '60', title: 'Laundry', status: 'pending' },
    ];
    renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
    const box = await screen.findByTestId('chore-checks');
    expect(screen.getAllByTestId('chore-check').map((li) => li.textContent)).toEqual([
      expect.stringContaining('Trash'), expect.stringContaining('Dishes'),
    ]); // only this child's
    expect(screen.getByText('2 to check')).toBeInTheDocument();
    expect(box).toHaveTextContent('Done right? Jordan ticked these off');

    fireEvent.click(screen.getByRole('button', { name: 'Trash was done right' }));
    await waitFor(() => expect(decided).toEqual([{ id: '7', body: { approve: true } }]));
    fireEvent.click(await screen.findByRole('button', { name: 'Ask Jordan to redo Dishes' }));
    await waitFor(() => expect(decided[1]).toEqual({ id: '8', body: { approve: false } }));
    await waitFor(() => expect(screen.queryByTestId('chore-checks')).toBeNull());
  });

  it('shows no approvals box when nothing is waiting', async () => {
    renderWithProviders(<ChoresCard username="jordan" name="Jordan" />);
    await screen.findByText('Feed the chickens');
    expect(screen.queryByTestId('chore-checks')).toBeNull();
  });
});

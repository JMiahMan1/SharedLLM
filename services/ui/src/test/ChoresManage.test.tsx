/**
 * The dashboard chores widget lets parents add and remove Skylight chores
 * (docs/ACHIEVEMENTS.md, "Managing chores"); everyone else sees no controls.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ChoreItem } from '../types/widget';

const authState: { user: Record<string, unknown> | null } = { user: null };
const getSkylightChores = vi.fn();
const getSkylightMembers = vi.fn();
const addSkylightChore = vi.fn();
const removeSkylightChore = vi.fn();

vi.mock('../context/AuthContext', () => ({ useAuth: () => authState }));
vi.mock('../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../services/api', () => ({
  api: {
    getSkylightChores: (...a: unknown[]) => getSkylightChores(...a),
    getSkylightMembers: (...a: unknown[]) => getSkylightMembers(...a),
    addSkylightChore: (...a: unknown[]) => addSkylightChore(...a),
    removeSkylightChore: (...a: unknown[]) => removeSkylightChore(...a),
  },
}));

import ChoresProgressWidget from '../components/widgets/ChoresProgressWidget';

const CHORES: ChoreItem[] = [
  { id: '50-2026-11-03', title: 'Feed the chickens', completed: false, reward: 2, assignees: ['Jordan'], recurrence: 'RRULE:FREQ=DAILY' },
  { id: '51', title: 'Trash', completed: false, assignees: ['Taylor'] },
];

const renderWidget = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ChoresProgressWidget settingsButton={null} userSettings={{ config: {}, widget_key: 'chores_progress' } as never} onTogglePin={vi.fn()} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  authState.user = { username: 'mom', is_admin: false, family_role: 'mother' };
  getSkylightChores.mockResolvedValue({ status: 'SUCCESS', chores: CHORES, assignee_meta: {} });
  getSkylightMembers.mockResolvedValue([
    { id: '11', label: 'Jordan', color: '#f00' },
    { id: '12', label: 'Taylor', color: null },
  ]);
  addSkylightChore.mockResolvedValue(undefined);
  removeSkylightChore.mockResolvedValue(undefined);
});

describe('chores widget for parents', () => {
  it('adds a chore for the people picked from the chore chart', async () => {
    const user = userEvent.setup();
    renderWidget();
    await screen.findByText('Feed the chickens');
    await user.click(screen.getByTestId('chore-add'));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Chore name'), 'Water the garden');
    await user.click(await within(dialog).findByRole('button', { name: 'Taylor' }));
    await user.click(within(dialog).getByRole('button', { name: 'Jordan' }));
    await user.selectOptions(within(dialog).getByLabelText('How often'), 'weekends');
    await user.click(within(dialog).getByRole('button', { name: /Add chore/ }));
    await waitFor(() => expect(addSkylightChore).toHaveBeenCalledWith({
      title: 'Water the garden', assignees: ['Taylor', 'Jordan'], repeat: 'weekends',
    }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(getSkylightChores.mock.calls.length).toBeGreaterThan(1); // the list refreshed
  });

  it('will not add a chore for no one', async () => {
    const user = userEvent.setup();
    renderWidget();
    await screen.findByText('Trash');
    await user.click(screen.getByTestId('chore-add'));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByRole('button', { name: 'Jordan' });
    await user.type(within(dialog).getByLabelText('Chore name'), 'Sweep');
    await user.click(within(dialog).getByRole('button', { name: /Add chore/ }));
    expect(addSkylightChore).not.toHaveBeenCalled();
  });

  it('adds from a person\'s column and removes from the full view', async () => {
    const user = userEvent.setup();
    renderWidget();
    await screen.findByText('Trash');
    await user.click(screen.getByRole('button', { name: 'Expand widget' }));

    const [forNoah] = await screen.findAllByRole('button', { name: /Add for Jordan/ });
    await user.click(forNoah);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('group', { name: 'Who is it for' })).toBeNull(); // already chosen
    await user.type(within(dialog).getByLabelText('Chore name'), 'Brush the dog');
    await user.click(within(dialog).getByRole('button', { name: /Add chore/ }));
    await waitFor(() => expect(addSkylightChore).toHaveBeenCalledWith({ title: 'Brush the dog', assignees: ['Jordan'] }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    const removeTrash = screen.getAllByRole('button', { name: 'Remove Trash' }).at(-1)!;
    await user.click(removeTrash);
    await waitFor(() => expect(removeSkylightChore).toHaveBeenCalledWith('51', undefined));

    await user.click(screen.getAllByRole('button', { name: 'Remove Feed the chickens' }).at(-1)!);
    const ask = await screen.findByRole('dialog');
    expect(ask).toHaveTextContent('repeats');
    await user.click(within(ask).getByRole('button', { name: 'Just today' }));
    await waitFor(() => expect(removeSkylightChore).toHaveBeenCalledWith('50-2026-11-03', 'one'));
  });

  it('shows no chore controls to a child', async () => {
    authState.user = { username: 'jordan', is_admin: false, family_role: 'child' };
    const user = userEvent.setup();
    renderWidget();
    await screen.findByText('Trash');
    expect(screen.queryByTestId('chore-add')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Expand widget' }));
    expect(screen.queryByTestId('chore-remove')).toBeNull();
    expect(screen.queryByTestId('chore-add-for')).toBeNull();
    expect(getSkylightMembers).not.toHaveBeenCalled();
  });
});

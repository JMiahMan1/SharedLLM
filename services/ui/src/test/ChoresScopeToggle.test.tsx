import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { choreBelongsTo, hasChoresFor } from '../lib/choreScope';
import type { ChoreItem } from '../types/widget';

const authState: { user: { username: string; is_admin: boolean } | null } = {
  user: { username: 'alex', is_admin: true },
};

const getSkylightChores = vi.fn();

vi.mock('../context/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('../hooks/useHaptics', () => ({
  useHaptics: () => ({ trigger: vi.fn() }),
}));

vi.mock('../services/api', () => ({
  api: {
    getSkylightChores: (...args: unknown[]) => getSkylightChores(...args),
  },
}));

import ChoresProgressWidget from '../components/widgets/ChoresProgressWidget';

const ALEXS_CHORE: ChoreItem = {
  id: '1-2026-10-04',
  title: 'Clean Room',
  completed: false,
  reward: 5,
  assignees: ['Alex'],
};
const SAM_DS_CHORE: ChoreItem = {
  id: '2-2026-10-04',
  title: 'Do Dishes',
  completed: false,
  reward: 5,
  assignees: ['Sam'],
};

function signInAs(username: string, isAdmin: boolean) {
  authState.user = { username, is_admin: isAdmin };
}

/** Answer each fetch the way the gateway would for that scope. */
function respondByScope() {
  getSkylightChores.mockImplementation(async (_date?: string, scope?: string) => ({
    status: 'SUCCESS',
    chores: scope === 'me' ? [ALEXS_CHORE] : [ALEXS_CHORE, SAM_DS_CHORE],
    assignee_meta: { Alex: '#ffcc00', Sam: '#88ccff' },
  }));
}

const renderWidget = () =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ChoresProgressWidget
        settingsButton={null}
        userSettings={{ config: {}, widget_key: 'chores_progress' } as never}
        onTogglePin={vi.fn()}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  getSkylightChores.mockReset();
  signInAs('alex', true);
  respondByScope();
});

describe('choreBelongsTo', () => {
  it('matches the same name in any casing', () => {
    expect(choreBelongsTo(['Alex'], 'alex')).toBe(true);
    expect(choreBelongsTo(['alex'], 'ALEX')).toBe(true);
  });

  it('matches a login name against a longer free-text label, as the server does', () => {
    // Skylight labels are typed by hand in another app; the execution service's
    // filter is lenient in both directions and these two must agree.
    expect(choreBelongsTo(['Alex S'], 'alex')).toBe(true);
    expect(choreBelongsTo(['Dad'], 'dad')).toBe(true);
    expect(choreBelongsTo(['Sam'], 'alex')).toBe(false);
  });

  it('ignores blank labels and a blank login name', () => {
    expect(choreBelongsTo(['', '  '], 'alex')).toBe(false);
    expect(choreBelongsTo(['Alex'], '')).toBe(false);
    expect(choreBelongsTo(undefined, 'alex')).toBe(false);
  });

  it('hasChoresFor looks across the whole list', () => {
    expect(hasChoresFor([SAM_DS_CHORE, ALEXS_CHORE], 'alex')).toBe(true);
    expect(hasChoresFor([SAM_DS_CHORE], 'alex')).toBe(false);
    expect(hasChoresFor([], 'alex')).toBe(false);
    expect(hasChoresFor(undefined, 'alex')).toBe(false);
  });
});

describe('chores widget: whose chores', () => {
  it('asks for the whole frame by default, with no scope parameter', async () => {
    renderWidget();
    await screen.findByText('Clean Room');
    expect(getSkylightChores).toHaveBeenCalledWith('today', undefined);
  });

  it('offers an admin a my-chores toggle when their name is on the board', async () => {
    renderWidget();
    await screen.findByText('Clean Room');
    expect(screen.getByTestId('chores-scope-toggle')).toBeInTheDocument();
    expect(screen.getByTestId('chores-scope-all')).toHaveAttribute('aria-pressed', 'true');
  });

  it('narrows to the admin’s own chores and keeps the toggle to switch back', async () => {
    const user = userEvent.setup();
    renderWidget();
    await screen.findByText('Do Dishes');

    await user.click(screen.getByTestId('chores-scope-me'));

    await waitFor(() => {
      expect(getSkylightChores).toHaveBeenCalledWith('today', 'me');
    });
    await waitFor(() => {
      expect(screen.queryByText('Do Dishes')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Clean Room')).toBeInTheDocument();
    // Still offered while narrowed -- otherwise there is no way back.
    expect(screen.getByTestId('chores-scope-toggle')).toBeInTheDocument();

    await user.click(screen.getByTestId('chores-scope-all'));
    await waitFor(() => {
      expect(screen.getByText('Do Dishes')).toBeInTheDocument();
    });
    expect(getSkylightChores).toHaveBeenLastCalledWith('today', undefined);
  });

  it('hides the toggle from a non-admin, who only ever sees their own', async () => {
    signInAs('sam', false);
    renderWidget();
    await screen.findByText('Clean Room');
    expect(screen.queryByTestId('chores-scope-toggle')).not.toBeInTheDocument();
    expect(getSkylightChores).toHaveBeenCalledWith('today', undefined);
  });

  it('hides the toggle from an admin with no chores of their own', async () => {
    getSkylightChores.mockResolvedValue({
      status: 'SUCCESS',
      chores: [SAM_DS_CHORE],
      assignee_meta: { Sam: '#88ccff' },
    });
    renderWidget();
    await screen.findByText('Do Dishes');
    // "my chores" would be an empty list, so the control is not offered at all.
    expect(screen.queryByTestId('chores-scope-toggle')).not.toBeInTheDocument();
  });
});
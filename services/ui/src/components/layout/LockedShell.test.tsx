import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import { renderWithProviders } from '../../test/render';
import LockedShell from './LockedShell';
import Family from '../../pages/Family';
import { normalizeSections, resetLockedModeForTests, setLockedMode } from '../../lib/lockedMode';

// The home cards have their own tests; here only which of them show matters.
vi.mock('../health/ScreenTimeCard', () => ({ default: () => <div>time card</div> }));
vi.mock('../widgets/ChoresProgressWidget', () => ({ default: () => <div>chores card</div> }));
vi.mock('../widgets/DeviceControlWidget', () => ({ default: () => <div>devices card</div> }));

const page = <div data-testid="the-page">page</div>;

describe('locked shell', () => {
  it('shows a locked screen with a way back to chat for a page that is not open', () => {
    renderWithProviders(<LockedShell sections={['chat']}>{page}</LockedShell>, { route: '/settings' });
    expect(screen.getByTestId('locked-page')).toBeInTheDocument();
    expect(screen.queryByTestId('the-page')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to chat/i })).toHaveAttribute('href', '/family');
  });

  it('never shows admin, even with every section open', () => {
    const all = normalizeSections(['calls', 'devices', 'time', 'chores', 'bible', 'calendar', 'music']);
    renderWithProviders(<LockedShell sections={all}>{page}</LockedShell>, { route: '/admin' });
    expect(screen.getByTestId('locked-page')).toBeInTheDocument();
  });

  it('shows an open section page', () => {
    renderWithProviders(<LockedShell sections={['chat', 'bible']}>{page}</LockedShell>, { route: '/bible' });
    expect(screen.getByTestId('the-page')).toBeInTheDocument();
  });

  it('offers only the open sections in its navigation, and no settings', () => {
    renderWithProviders(<LockedShell sections={['chat', 'bible', 'time']}>{page}</LockedShell>, { route: '/' });
    const nav = screen.getByTestId('locked-nav');
    const labels = within(nav).getAllByRole('link').map((a) => a.textContent);
    expect(labels).toEqual(['Home', 'Chat', 'Bible']);
    expect(within(nav).queryByText(/settings|admin|calls/i)).not.toBeInTheDocument();
  });

  it('puts chat first on the locked home and only the open cards under it', async () => {
    renderWithProviders(<LockedShell sections={['chat', 'time', 'chores']}>{page}</LockedShell>, { route: '/' });
    const home = screen.getByTestId('locked-home');
    expect(home.firstElementChild).toBe(screen.getByTestId('locked-home-chat'));
    expect(await screen.findByText('time card')).toBeInTheDocument();
    expect(await screen.findByText('chores card')).toBeInTheDocument();
    expect(screen.queryByText('devices card')).not.toBeInTheDocument();
    expect(screen.queryByTestId('the-page')).not.toBeInTheDocument();
  });
});

describe('family tabs in locked mode', () => {
  afterEach(() => act(() => resetLockedModeForTests()));

  it('hides the tabs that are not open', () => {
    act(() => setLockedMode({ active: true, sections: ['chat', 'calls'] }));
    renderWithProviders(<Family />);
    const tabs = screen.getAllByRole('tab').map((t) => t.getAttribute('aria-label'));
    expect(tabs).toEqual(['Chat', 'Calls']);
  });

  it('shows every tab when not locked', () => {
    renderWithProviders(<Family />);
    expect(screen.getAllByRole('tab')).toHaveLength(5);
  });
});

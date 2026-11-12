import { describe, it, expect, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from './render';
import BottomNav from '../components/layout/BottomNav';
import { MOBILE_NAV_STORAGE_KEY, writeMobileNavPaths } from '../lib/mobileNav';

describe('BottomNav', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('pins the five main tabs and a More button', () => {
    renderWithProviders(<BottomNav />);

    for (const label of ['Home', 'Wander', 'Family', 'Media', 'Health']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: /more pages/i })).toBeInTheDocument();
    expect(screen.queryByTestId('more-sheet')).not.toBeInTheDocument();
  });

  it('opens the More sheet with secondary pages and closes on select', async () => {
    const user = userEvent.setup();
    renderWithProviders(<BottomNav />);

    await user.click(screen.getByRole('button', { name: /more pages/i }));
    expect(screen.getByTestId('more-sheet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /calendar/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /notes/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /settings/i })).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: /notes/i }));
    expect(screen.queryByTestId('more-sheet')).not.toBeInTheDocument();
  });
});

describe('BottomNav reach', () => {
  it('puts every desktop page a family member may open in More', async () => {
    const user = userEvent.setup();
    renderWithProviders(<BottomNav />);

    await user.click(screen.getByRole('button', { name: /more pages/i }));
    for (const label of [/knowledge/i, /workspaces/i, /docs/i, /bible/i]) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
  });
});

describe('BottomNav custom layout', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('follows a saved order and moves displaced pages into More', async () => {
    localStorage.setItem(
      MOBILE_NAV_STORAGE_KEY,
      JSON.stringify(['/notes', '/', '/family', '/media', '/fitness']),
    );
    const user = userEvent.setup();
    renderWithProviders(<BottomNav />);

    expect(screen.getByRole('link', { name: 'Notes' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Wander' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /more pages/i }));
    expect(screen.getByRole('link', { name: 'Wander' })).toBeInTheDocument();
  });

  it('updates immediately when the layout changes', async () => {
    renderWithProviders(<BottomNav />);

    writeMobileNavPaths(['/notes', '/calendar'], true);

    expect(await screen.findByRole('link', { name: 'Notes' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Calendar' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Wander' })).not.toBeInTheDocument();
  });
});

describe('BottomNav sections', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('lists the More pages under the same headings as the sidebar, home controls together', async () => {
    const user = userEvent.setup();
    renderWithProviders(<BottomNav />);
    await user.click(screen.getByRole('button', { name: /more pages/i }));
    const home = await screen.findByTestId('more-section-home');
    for (const label of ['Rooms', 'Climate', 'Cameras', 'Energy']) {
      expect(within(home).getByRole('link', { name: label })).toBeInTheDocument();
    }
    const manage = screen.getByTestId('more-section-manage');
    expect(within(manage).getByRole('link', { name: 'Automations' })).toBeInTheDocument();
    expect(within(manage).getByRole('link', { name: 'Schedules' })).toBeInTheDocument();
  });

  it('keeps the bar itself at five pages plus More', () => {
    renderWithProviders(<BottomNav />);
    const bar = screen.getByRole('button', { name: /more pages/i }).parentElement as HTMLElement;
    expect(within(bar).getAllByRole('link')).toHaveLength(5);
  });
});

import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
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

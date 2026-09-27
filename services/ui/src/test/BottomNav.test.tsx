import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from './render';
import BottomNav from '../components/layout/BottomNav';

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

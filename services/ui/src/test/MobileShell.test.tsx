import { afterEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from './render';
import MobileShell from '../components/layout/MobileShell';

const setWidth = (width: number) => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  window.dispatchEvent(new Event('resize'));
};

describe('MobileShell', () => {
  const original = window.innerWidth;
  afterEach(() => setWidth(original));

  it('shows the bottom bar on a phone-width screen', () => {
    setWidth(360);
    renderWithProviders(<MobileShell><p>page</p></MobileShell>);
    expect(screen.getByRole('button', { name: /more pages/i })).toBeInTheDocument();
  });

  it('keeps the bottom bar on a tablet-width screen, where it is the only way between pages', () => {
    setWidth(1180);
    renderWithProviders(<MobileShell><p>page</p></MobileShell>);
    expect(screen.getByRole('button', { name: /more pages/i })).toBeInTheDocument();
    expect(screen.getByText('page')).toBeInTheDocument();
  });
});

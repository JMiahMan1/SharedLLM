import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { isUnreachable } from '../../hooks/useOfflineMode';

const state = vi.hoisted(() => ({ offline: false }));
vi.mock('../../hooks/useOfflineMode', async (orig) => ({
  ...(await orig<typeof import('../../hooks/useOfflineMode')>()),
  useOfflineMode: () => ({ offline: state.offline, android: true }),
}));

import OfflineModeBanner from './OfflineModeBanner';

describe('OfflineModeBanner', () => {
  it('shows only while the server cannot be reached', () => {
    const { rerender } = render(<MemoryRouter><OfflineModeBanner /></MemoryRouter>);
    expect(screen.queryByTestId('offline-mode-banner')).toBeNull();
    state.offline = true;
    rerender(<MemoryRouter><OfflineModeBanner /></MemoryRouter>);
    expect(screen.getByTestId('offline-mode-banner')).toHaveTextContent('Offline, using nearby family phones');
    expect(screen.getByTestId('offline-mode-banner')).toHaveAttribute('href', '/wander#offline');
  });

  it('counts only a request with no answer at all as unreachable', () => {
    expect(isUnreachable(new Error('Network Error'))).toBe(true);
    expect(isUnreachable({ response: { status: 503 } })).toBe(false);
    expect(isUnreachable(null)).toBe(false);
  });
});

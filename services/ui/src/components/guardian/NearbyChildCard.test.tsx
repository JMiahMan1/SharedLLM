import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const api = vi.hoisted(() => ({ getNearbySettings: vi.fn(), setNearbySettings: vi.fn() }));
vi.mock('../../services/api', () => ({ api }));

import NearbyChildCard from './NearbyChildCard';

const state = {
  username: 'robin', settings: { nearby: false, chat: false, offline: false, car: true, mine: false },
  managed_by_parent: true, chosen: true, can_change: true, always_on: ['parent_link'],
};
const draw = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NearbyChildCard username="robin" name="Robin" />
    </QueryClientProvider>,
  );

describe('NearbyChildCard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.getNearbySettings.mockResolvedValue(state);
  });

  it("a parent sets a child's parts", async () => {
    api.setNearbySettings.mockResolvedValue({ ...state, settings: { ...state.settings, offline: true } });
    draw();
    await userEvent.click(await screen.findByRole('switch', { name: 'Offline mode' }));
    expect(api.getNearbySettings).toHaveBeenCalledWith('robin');
    expect(api.setNearbySettings).toHaveBeenCalledWith({ offline: true }, 'robin');
    expect(await screen.findByRole('switch', { name: 'Offline mode' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('guardian-nearby-parent-link')).toHaveTextContent("reach Robin's phone");
  });

  it('a parent who may not change them sees them only', async () => {
    api.getNearbySettings.mockResolvedValue({ ...state, can_change: false });
    draw();
    expect(await screen.findByRole('switch', { name: 'Car detection' })).toBeDisabled();
  });
});

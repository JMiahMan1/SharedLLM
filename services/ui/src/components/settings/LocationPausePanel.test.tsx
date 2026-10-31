import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LocationPausePanel from './LocationPausePanel';
import { api } from '../../services/api';

vi.mock('../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getLocationPause: vi.fn(), setLocationPause: vi.fn(), clearLocationPause: vi.fn() } };
});

const renderIt = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LocationPausePanel />
    </QueryClientProvider>,
  );

describe('LocationPausePanel', () => {
  beforeEach(() => vi.resetAllMocks());

  it('lets an adult pause for a while, then share again', async () => {
    vi.mocked(api.getLocationPause).mockResolvedValue({ paused: false, until: null, allowed: true });
    vi.mocked(api.setLocationPause).mockResolvedValue({ paused: true, until: null });
    renderIt();
    await userEvent.click(await screen.findByRole('button', { name: '8 hours' }));
    expect(api.setLocationPause).toHaveBeenCalledWith(480);
  });

  it('shows the pause and turns it off', async () => {
    vi.mocked(api.getLocationPause).mockResolvedValue({ paused: true, until: null, allowed: true });
    vi.mocked(api.clearLocationPause).mockResolvedValue({ paused: false });
    renderIt();
    expect(await screen.findByTestId('location-pause-state')).toHaveTextContent('Paused until you turn it back on');
    await userEvent.click(screen.getByRole('button', { name: /share my location again/i }));
    await waitFor(() => expect(api.clearLocationPause).toHaveBeenCalled());
  });

  it('is not offered to children and teens', async () => {
    vi.mocked(api.getLocationPause).mockResolvedValue({ paused: false, until: null, allowed: false });
    const { container } = renderIt();
    await waitFor(() => expect(api.getLocationPause).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

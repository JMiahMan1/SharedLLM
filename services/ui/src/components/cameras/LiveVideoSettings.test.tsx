import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LiveVideoSettings from './LiveVideoSettings';
import * as cameraLive from '../../lib/cameraLive';
import type { LiveVideoSettings as Settings } from '../../lib/cameraLive';

vi.mock('../../lib/cameraLive', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getLiveVideoSettings: vi.fn(), saveLiveVideoSettings: vi.fn(), syncLiveVideo: vi.fn() };
});

const SETTINGS: Settings = {
  configured: true, entry_id: 'e1', enabled: true, state: 'setup_in_progress', reason: null,
  url: 'http://go2rtc.example:1984', username: '', has_password: false,
  options: { webrtc_host: '', webrtc_port: 8555, default_quality: 'sub' },
  server: { version: null, error: null }, cameras: [],
};

describe('LiveVideoSettings', () => {
  it('keeps what is typed when a refetch brings only a new status', async () => {
    vi.mocked(cameraLive.getLiveVideoSettings).mockResolvedValue(SETTINGS);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><LiveVideoSettings /></QueryClientProvider>);
    const host = await screen.findByPlaceholderText('automatic') as HTMLInputElement;
    fireEvent.change(host, { target: { value: 'hub.example' } });

    // Coming back to the app refetches; go2rtc has finished starting meanwhile.
    vi.mocked(cameraLive.getLiveVideoSettings).mockResolvedValue({ ...SETTINGS, state: 'loaded', server: { version: '1.9', error: null } });
    await client.refetchQueries();
    await waitFor(() => expect(screen.getByText(/go2rtc 1\.9/)).toBeTruthy());
    expect(host.value).toBe('hub.example');
  });

  it('follows a change to what the hub stores', async () => {
    vi.mocked(cameraLive.getLiveVideoSettings).mockResolvedValue(SETTINGS);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><LiveVideoSettings /></QueryClientProvider>);
    const host = await screen.findByPlaceholderText('automatic') as HTMLInputElement;
    vi.mocked(cameraLive.getLiveVideoSettings).mockResolvedValue({ ...SETTINGS, options: { ...SETTINGS.options, webrtc_host: 'set.elsewhere' } });
    await client.refetchQueries();
    await waitFor(() => expect(host.value).toBe('set.elsewhere'));
  });
});

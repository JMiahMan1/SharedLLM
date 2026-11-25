import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WhisperModePanel } from './WhisperModePanel';
import { api } from '../../services/api';
import type { HubVoiceSettings } from '../../types/api';

vi.mock('../../services/api', () => ({
  api: {
    getHubVoiceSettings: vi.fn(),
    updateHubVoiceSettings: vi.fn(),
  },
}));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const SETTINGS: HubVoiceSettings = {
  whisper: { enabled: true, volume: 0.22, level_drop_db: 8 },
  defaults: { whisper: { enabled: true, volume: 0.22, level_drop_db: 8 } },
  level_drop_range_db: [3, 30],
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><WhisperModePanel /></QueryClientProvider>);
}

describe('WhisperModePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getHubVoiceSettings).mockResolvedValue(SETTINGS);
    vi.mocked(api.updateHubVoiceSettings).mockImplementation(async (change) => ({
      ...SETTINGS, whisper: { ...SETTINGS.whisper, ...change },
    }));
  });

  it('shows whisper mode on by default with its volume', async () => {
    renderPanel();
    const toggle = await screen.findByRole('switch', { name: 'Whisper mode' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('whisper-volume-value').textContent).toBe('22%');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('turns whisper mode off in HomeHub', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('switch', { name: 'Whisper mode' }));
    await waitFor(() => expect(api.updateHubVoiceSettings).toHaveBeenCalledWith({ enabled: false }));
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Whisper mode' }).getAttribute('aria-checked')).toBe('false'));
  });

  it('saves the reply volume and what counts as a whisper', async () => {
    renderPanel();
    fireEvent.change(await screen.findByLabelText('Reply volume while whispering'), { target: { value: '0.35' } });
    fireEvent.change(screen.getByLabelText('What counts as a whisper'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateHubVoiceSettings).toHaveBeenCalledWith({ volume: 0.35, level_drop_db: 12 }));
  });

  it('keeps a custom sensitivity set through the API selectable', async () => {
    vi.mocked(api.getHubVoiceSettings).mockResolvedValue({ ...SETTINGS, whisper: { ...SETTINGS.whisper, level_drop_db: 20 } });
    renderPanel();
    expect(await screen.findByRole('option', { name: 'Custom (20 dB)' })).toBeTruthy();
  });

  it('says when HomeHub cannot be reached', async () => {
    vi.mocked(api.getHubVoiceSettings).mockRejectedValue(new Error('The home hub did not answer'));
    renderPanel();
    expect(await screen.findByText(/did not answer|Could not load/)).toBeTruthy();
  });
});

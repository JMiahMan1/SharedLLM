import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VoiceRecognitionPanel } from './VoiceRecognitionPanel';
import { newVoiceLine } from '../../lib/voiceAdmin';
import { api } from '../../services/api';
import type { VoiceAdminOverview } from '../../types/api';

vi.mock('../../services/api', () => ({
  api: {
    getVoiceAdmin: vi.fn(),
    recalibrateVoice: vi.fn(async () => ({})),
    approveVoiceCandidate: vi.fn(async () => ({ status: 'SUCCESS', samples: 5 })),
    rejectVoiceCandidate: vi.fn(async () => ({ status: 'SUCCESS' })),
    clearAutoVoiceSamples: vi.fn(async () => ({ status: 'SUCCESS', samples: 3 })),
    forgetNewVoice: vi.fn(async () => ({ status: 'SUCCESS' })),
  },
}));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const OVERVIEW: VoiceAdminOverview = {
  calibration: {
    status: 'single', threshold: 0.8, margin: 0.04, people: 1,
    same_speaker: { n: 3, min: 0.88, p10: 0.88, median: 0.89, max: 0.93 }, other_speaker: null,
    per_person: [{ username: 'member-a', samples: 3, same_min: 0.88, group: 'adult' }],
  },
  min_samples: 3,
  people: [
    { username: 'member-a', name: 'Member A', family_role: 'father', samples: 4, auto_samples: 1, satellite_samples: 0, enrolled: true, consistency: 0.9 },
    { username: 'member-b', name: 'Member B', family_role: 'child', samples: 0, auto_samples: 0, satellite_samples: 0, enrolled: false, consistency: null },
  ],
  candidates: [
    { id: 7, username: 'member-a', name: 'Member A', score: 0.66, runner_up: null, speech_seconds: 2.1, satellite: 'assist_satellite.test_den', created_at: '2026-11-24T10:00:00Z' },
  ],
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><VoiceRecognitionPanel /></QueryClientProvider>);
}

describe('VoiceRecognitionPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getVoiceAdmin).mockResolvedValue(OVERVIEW);
  });

  it('shows the calibration, who is enrolled and how to enrol the rest', async () => {
    renderPanel();
    expect(await screen.findByText('80%')).toBeTruthy();
    expect(screen.getByText(/Only one person has enrolled/)).toBeTruthy();
    expect(screen.getByText('Not enrolled')).toBeTruthy();
    expect(screen.getByText(/learn my voice/)).toBeTruthy();
    expect(screen.getByText(/1 auto-added/)).toBeTruthy();
  });

  it('approves or rejects a candidate sample', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Approve sample for Member A' }));
    await waitFor(() => expect(api.approveVoiceCandidate).toHaveBeenCalledWith(7));
    fireEvent.click(screen.getByRole('button', { name: 'Reject sample for Member A' }));
    await waitFor(() => expect(api.rejectVoiceCandidate).toHaveBeenCalledWith(7));
  });

  it('calibrates again and removes auto-added samples on request', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /Calibrate now/ }));
    await waitFor(() => expect(api.recalibrateVoice).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Remove auto-added' }));
    await waitFor(() => expect(api.clearAutoVoiceSamples).toHaveBeenCalledWith('member-a'));
  });

  it('lists a new voice by its counts only and can dismiss it', async () => {
    vi.mocked(api.getVoiceAdmin).mockResolvedValue({
      ...OVERVIEW,
      new_voices: [{ id: 'c1', device: 'hub:mac:02:00:00:00:00:31', label: 'Office Echo', visits_week: 5, last_heard: '2026-11-24T10:00:00Z' }],
    });
    renderPanel();
    expect(await screen.findByText('A new voice talked to the Office Echo 5 times this week.')).toBeTruthy();
    expect(screen.getByText(/Counts only/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss the new voice at Office Echo' }));
    await waitFor(() => expect(api.forgetNewVoice).toHaveBeenCalledWith('c1', 'hub:mac:02:00:00:00:00:31'));
  });

  it('says nothing about new voices when there are none', async () => {
    renderPanel();
    await screen.findByText('80%');
    expect(screen.queryByTestId('voice-new-voices')).toBeNull();
  });
});

describe('newVoiceLine', () => {
  it('names the satellite and says how often', () => {
    const base = { id: 'x', device: 'hub:dev', label: 'Kitchen', visits_week: 1, last_heard: '' };
    expect(newVoiceLine(base)).toBe('A new voice talked to the Kitchen once this week.');
    expect(newVoiceLine({ ...base, label: null, device: 'assist_satellite.den_assist_satellite', visits_week: 3 }))
      .toBe('A new voice talked to the den 3 times this week.');
    expect(newVoiceLine({ ...base, label: null })).toBe('A new voice talked to a satellite once this week.');
  });
});

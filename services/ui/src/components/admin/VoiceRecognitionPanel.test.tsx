import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VoiceRecognitionPanel } from './VoiceRecognitionPanel';
import { api } from '../../services/api';
import type { VoiceAdminOverview } from '../../types/api';

vi.mock('../../services/api', () => ({
  api: {
    getVoiceAdmin: vi.fn(),
    recalibrateVoice: vi.fn(async () => ({})),
    approveVoiceCandidate: vi.fn(async () => ({ status: 'SUCCESS', samples: 5 })),
    rejectVoiceCandidate: vi.fn(async () => ({ status: 'SUCCESS' })),
    clearAutoVoiceSamples: vi.fn(async () => ({ status: 'SUCCESS', samples: 3 })),
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
});

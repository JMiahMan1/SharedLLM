import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import VoiceSignaturePanel from '../components/settings/VoiceSignaturePanel';

const status = (samples: number, extra: Record<string, unknown> = {}) => ({
  enrolled: samples >= 3,
  samples,
  min_samples: 3,
  max_samples: 6,
  consistency: samples >= 2 ? 0.94 : null,
  model: 'resemblyzer-ge2e-256',
  updated_at: samples ? '2026-10-14T20:00:00Z' : null,
  ...extra,
});

/** A MediaRecorder that hands over one chunk of "audio" when stopped. */
class FakeRecorder {
  static isTypeSupported = (t: string) => t.startsWith('audio/webm');
  state = 'inactive';
  mimeType: string;
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(_s: MediaStream, opts?: { mimeType?: string }) {
    this.mimeType = opts?.mimeType ?? 'audio/webm';
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['voice'], { type: this.mimeType }) });
    this.onstop?.();
  }
}

let original: unknown;
beforeEach(() => {
  original = window.MediaRecorder;
  (window as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder;
});
afterEach(() => {
  (window as unknown as { MediaRecorder: unknown }).MediaRecorder = original;
});

async function recordAndStop(button: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name: button }));
  fireEvent.click(await screen.findByRole('button', { name: /Stop/ }));
}

describe('VoiceSignaturePanel', () => {
  it('walks through the recordings, sending each one to be signed', async () => {
    let current = status(1);
    let uploads = 0;
    server.use(
      http.get('/api/users/me/voice', () => HttpResponse.json(current)),
      http.post('/api/users/me/enroll', async ({ request }) => {
        const form = await request.formData();
        expect(form.get('file')).toBeInstanceOf(File);
        uploads += 1;
        current = status(2);
        return HttpResponse.json({ status: 'SUCCESS', speech_seconds: 4.1, ...current });
      }),
    );
    renderWithProviders(<VoiceSignaturePanel />);
    expect(await screen.findByTestId('voice-status')).toHaveTextContent('1 of 3 recordings');
    expect(screen.getByText(/Recording 2 of 3: read this aloud/)).toBeInTheDocument();
    const first = screen.getByTestId('voice-phrase').textContent;

    await recordAndStop(/Record next/);
    await waitFor(() => expect(uploads).toBe(1));
    expect(await screen.findByTestId('voice-status')).toHaveTextContent('2 of 3 recordings');
    expect(screen.getByTestId('voice-phrase').textContent).not.toBe(first);
  });

  it("shows the server's reason when a recording is refused", async () => {
    server.use(
      http.get('/api/users/me/voice', () => HttpResponse.json(status(0))),
      http.post('/api/users/me/enroll', () =>
        HttpResponse.json({ detail: 'Only 0.8s of speech was heard; at least 2s is needed.' }, { status: 422 }),
      ),
    );
    renderWithProviders(<VoiceSignaturePanel />);
    await recordAndStop(/Start recording/);
    expect(await screen.findByText(/0.8s of speech/)).toBeInTheDocument();
    expect(screen.getByTestId('voice-status')).toHaveTextContent('Not set up');
  });

  it('tests an enrolled voice against the household', async () => {
    server.use(
      http.get('/api/users/me/voice', () => HttpResponse.json(status(3))),
      http.post('/api/users/me/voice/check', () =>
        HttpResponse.json({
          matched: true, matched_username: 'jeremiah', matched_name: 'Jeremiah', score: 0.93,
          threshold: 0.8, margin: 0.2, candidates: [{ username: 'jeremiah', name: 'Jeremiah', score: 0.93 }], is_me: true,
        }),
      ),
    );
    renderWithProviders(<VoiceSignaturePanel />);
    expect(await screen.findByTestId('voice-status')).toHaveTextContent('Enrolled · 3 recordings');
    expect(screen.getByText(/agree 94%/)).toBeInTheDocument();
    await recordAndStop(/Test: who am I/);
    expect(await screen.findByTestId('voice-check')).toHaveTextContent('Recognised you (93% match)');
  });

  it('says plainly when it heard someone else', async () => {
    server.use(
      http.get('/api/users/me/voice', () => HttpResponse.json(status(3))),
      http.post('/api/users/me/voice/check', () =>
        HttpResponse.json({
          matched: false, matched_username: null, matched_name: null, score: 0.71, threshold: 0.8, margin: 0.1,
          candidates: [{ username: 'michele', name: 'Michele', score: 0.71 }], is_me: false,
        }),
      ),
    );
    renderWithProviders(<VoiceSignaturePanel />);
    await recordAndStop(/Test: who am I/);
    expect(await screen.findByTestId('voice-check')).toHaveTextContent('Closest is Michele at 71%');
  });

  it('erases the profile only after a second, explicit tap', async () => {
    let erased = false;
    server.use(
      http.get('/api/users/me/voice', () => HttpResponse.json(erased ? status(0) : status(3))),
      http.delete('/api/users/me/voice', () => {
        erased = true;
        return HttpResponse.json({ status: 'SUCCESS', ...status(0) });
      }),
    );
    renderWithProviders(<VoiceSignaturePanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Forget my voice/ }));
    expect(erased).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Erase voice profile' }));
    await waitFor(() => expect(erased).toBe(true));
    expect(await screen.findByTestId('voice-status')).toHaveTextContent('Not set up');
  });
});

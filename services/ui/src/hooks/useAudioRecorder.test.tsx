import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useAudioRecorder } from './useAudioRecorder';

class FakeRecorder {
  static isTypeSupported = (type: string) => type === 'audio/webm;codecs=opus';
  static last: FakeRecorder | null = null;
  /** When true the take comes back empty (no data), as a muted microphone does. */
  static empty = false;
  state = 'inactive';
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(_stream: unknown, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? '';
    FakeRecorder.last = this;
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(FakeRecorder.empty ? [] : ['abc'], { type: this.mimeType }) });
    this.onstop?.();
  }
}

describe('useAudioRecorder', () => {
  const stopTrack = vi.fn();
  // The test setup installs writable (not configurable) stand-ins; swap and restore them.
  const saved = { recorder: window.MediaRecorder, devices: navigator.mediaDevices };
  beforeEach(() => {
    vi.useFakeTimers();
    FakeRecorder.empty = false;
    (window as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder;
    (navigator as unknown as { mediaDevices: unknown }).mediaDevices = {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
    };
  });
  afterEach(() => {
    vi.useRealTimers();
    (window as unknown as { MediaRecorder: unknown }).MediaRecorder = saved.recorder;
    (navigator as unknown as { mediaDevices: unknown }).mediaDevices = saved.devices;
  });

  it('records Opus where offered and stops by itself after the limit, counting down', async () => {
    const onRecorded = vi.fn();
    const { result } = renderHook(() => useAudioRecorder({ onRecorded, onError: vi.fn() }));
    await act(async () => {
      await result.current.start(3);
    });
    expect(FakeRecorder.last?.mimeType).toBe('audio/webm;codecs=opus');
    expect(result.current.secondsLeft).toBe(3);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.secondsLeft).toBe(2);
    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });
    expect(onRecorded).toHaveBeenCalledTimes(1);
    expect(result.current.recording).toBe(false);
    expect(stopTrack).toHaveBeenCalled();
  });

  it('says why when the microphone is refused', async () => {
    (navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('no'));
    const onError = vi.fn();
    const { result } = renderHook(() => useAudioRecorder({ onRecorded: vi.fn(), onError }));
    await act(async () => {
      await result.current.start();
    });
    expect(onError).toHaveBeenCalledWith('denied');
  });

  it('settles an empty take through onError instead of hanging', async () => {
    FakeRecorder.empty = true;
    const onRecorded = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useAudioRecorder({ onRecorded, onError }));
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      result.current.stop();
    });
    expect(onRecorded).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith('empty');
    expect(result.current.recording).toBe(false);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PrivateCamera from './PrivateCamera';

/**
 * The "Save to gallery" tick belongs to the picture it was ticked for.
 *
 * Retaking is a new picture, so the box goes back to unticked: otherwise a
 * parent ticks it once, retakes because the first shot was blurred, and the
 * good shot is filed into the shared gallery without ever being asked. The
 * video recorder and the voice-note recorder reset theirs the same way.
 */

const stop = vi.fn();

beforeEach(() => {
  // A camera that is on, and a still with real pixels: jsdom has neither.
  // jsdom ships `navigator.mediaDevices` without getUserMedia, and the property
  // cannot be replaced: the method is what gets stubbed.
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
    configurable: true,
    value: vi.fn(async () => ({ getTracks: () => [{ stop }] })),
  });
  // jsdom's srcObject setter insists on a real MediaStream, which no stub can be.
  Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', { configurable: true, writable: true, value: null });
  Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: () => Promise.resolve() });
  Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', { configurable: true, get: () => 640 });
  Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', { configurable: true, get: () => 480 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

const tick = () => screen.getByTestId('private-camera-gallery') as HTMLInputElement;

describe('the camera', () => {
  it('forgets the gallery tick when the picture is retaken', async () => {
    render(<PrivateCamera onCapture={vi.fn(async () => undefined)} onClose={vi.fn()} />);

    const shutter = await screen.findByTestId('private-camera-shutter');
    await waitFor(() => expect((shutter as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(shutter);

    const box = await screen.findByTestId('private-camera-gallery');
    fireEvent.click(box);
    expect(tick().checked).toBe(true);

    fireEvent.click(screen.getByTestId('private-camera-retake'));

    // Back on the viewfinder: the next picture starts with nothing ticked.
    const again = await screen.findByTestId('private-camera-shutter');
    await waitFor(() => expect((again as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(again);
    expect(tick().checked).toBe(false);
  });
});

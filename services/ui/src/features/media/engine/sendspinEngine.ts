import { resumeSendspinAudio } from './sendspinInternals';

/**
 * The web player's audio output: exactly one `<audio>` element at a time
 * (BUG-49) and one way to unlock playback from a user gesture (it replaced
 * four copies in `lib/maWebPlayer.ts`).
 *
 * The connection lifecycle (both sockets, backoff, MA readiness) stays in the
 * `useMAWebPlayer` hook, which already owns it; this module owns the element.
 */
export class SendspinAudioOutput {
  private element: HTMLAudioElement | null = null;

  get audio(): HTMLAudioElement | null {
    return this.element;
  }

  /** A fresh element for a new connection; any previous one is removed first. */
  create(playbackRate: number, onError: () => void): HTMLAudioElement {
    this.dispose();
    const audio = new Audio();
    audio.crossOrigin = 'anonymous';
    audio.playbackRate = playbackRate;
    audio.onerror = onError;
    document.body.appendChild(audio);
    this.element = audio;
    return audio;
  }

  setVolume(volume: number): void {
    if (this.element) this.element.volume = Math.max(0, Math.min(1, volume / 100));
  }

  setMuted(muted: boolean): void {
    if (this.element) this.element.muted = muted;
  }

  setPlaybackRate(rate: number): void {
    if (this.element) this.element.playbackRate = rate;
  }

  /**
   * Browsers only start audio inside a user gesture: resume the element and
   * Sendspin's AudioContext while we are in one.
   */
  unlock(player: unknown): void {
    try {
      if (this.element?.paused) void this.element.play().catch(() => {});
      resumeSendspinAudio(player);
    } catch {
      /* nothing to unlock yet */
    }
  }

  /** Remove the element; safe to call repeatedly. */
  dispose(): void {
    const audio = this.element;
    this.element = null;
    if (!audio) return;
    try {
      audio.onerror = null;
      audio.pause();
    } catch { /* jsdom / already stopped */ }
    try { audio.remove(); } catch { /* already gone */ }
  }
}

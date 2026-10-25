import { useEffect, useRef } from 'react';
import { postAbsProgress } from '../api/client';
import { livePosition } from '../store/ingest';
import { useMediaStore, type AbsSession } from '../store/mediaStore';
import type { SharedPlayer } from '../../../context/webPlayerContext';

/** How often a playing book's position is saved (§7.5). */
export const PROGRESS_INTERVAL_MS = 15_000;

export interface OutputReading {
  playing: boolean;
  position: number;
  duration: number;
  /** What the output plays; null when nothing is loaded. */
  uri: string | null;
}

/** Save `position` of `session`'s book; failures wait for the next save. */
function saveProgress(session: AbsSession, position: number, fallbackDuration: number): number | null {
  const duration = session.durationSec || fallbackDuration;
  if (!(duration > 0)) return null;
  const current = Math.max(0, Math.min(duration, Math.round(position)));
  void postAbsProgress({ item_id: session.itemId, current_time: current, duration }).catch(() => {});
  return current;
}

/** What `session`'s output is doing now. */
export function readOutput(
  session: AbsSession,
  web: Pick<SharedPlayer, 'isPlaying' | 'position' | 'duration' | 'mediaUri' | 'mediaTitle'> | null,
  now: number,
): OutputReading | null {
  if (!session.outputId) {
    if (!web) return null;
    return { playing: web.isPlaying, position: web.position, duration: web.duration, uri: web.mediaUri ?? (web.mediaTitle ? '' : null) };
  }
  const p = useMediaStore.getState().players[session.outputId];
  if (!p) return null;
  return {
    playing: p.status === 'playing',
    position: livePosition(p, now),
    duration: p.item?.durationSec ?? 0,
    uri: p.item ? p.item.uri : null,
  };
}

/**
 * Saves the position of a book started from this app back to Audiobookshelf
 * (`POST /api/media/abs/progress`) every 15 s while it plays, on any output,
 * and once when it pauses or stops, so another device resumes where this one
 * left off. The session ends when the output moves on to something else.
 */
export function useAbsProgressReporter(web: SharedPlayer | null): void {
  const session = useMediaStore((s) => s.absSession);
  const webRef = useRef(web);
  useEffect(() => {
    webRef.current = web;
  }, [web]);

  useEffect(() => {
    if (!session) return;
    let bookUri: string | null = null; // what the output called the book, once seen
    let wasPlaying = false;
    let lastSent = -1;

    const send = (reading: OutputReading) => {
      if (Math.round(reading.position) === lastSent) return;
      lastSent = saveProgress(session, reading.position, reading.duration) ?? lastSent;
    };

    const tick = () => {
      const reading = readOutput(session, webRef.current, Date.now());
      if (!reading) return;
      if (reading.uri && bookUri === null && reading.playing) bookUri = reading.uri;
      // The output now plays something else: this book is done here.
      if (bookUri !== null && reading.uri !== null && reading.uri !== bookUri) {
        useMediaStore.getState().actions.setAbsSession(null);
        return;
      }
      if (reading.playing || wasPlaying) send(reading);
      wasPlaying = reading.playing;
    };

    const id = window.setInterval(tick, PROGRESS_INTERVAL_MS);
    // A pause or stop is saved at once, not on the next tick.
    const unsubscribe = useMediaStore.subscribe((s) => {
      if (session.outputId && s.players[session.outputId]?.status !== 'playing' && wasPlaying) tick();
    });
    return () => {
      clearInterval(id);
      unsubscribe();
    };
  }, [session]);

  // This device pausing is a React change, not a store one.
  const playing = web?.isPlaying ?? false;
  const prev = useRef(playing);
  useEffect(() => {
    const s = useMediaStore.getState().absSession;
    if (prev.current && !playing && s && !s.outputId && web) saveProgress(s, web.position, web.duration);
    prev.current = playing;
  }, [playing, web]);
}

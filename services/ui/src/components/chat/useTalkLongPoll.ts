import { useEffect, useRef, useState } from 'react';
import { api } from '../../services/api';
import { detailList } from './chatModel';

const HOLD_S = 25;
const QUICK_EMPTY_MS = 1000;
const QUICK_EMPTY_PAUSE_MS = 2000;
const HIDDEN_PAUSE_MS = 2000;
const BACKOFF_START_MS = 3000;
const BACKOFF_MAX_MS = 30000;

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer);
      resolve();
    });
  });

/**
 * Keep one long poll open on the conversation on screen.
 *
 * Nextcloud holds each request until a message newer than `newestId` lands,
 * so a new trivia question (or anyone's reply) reaches every phone the moment
 * it is posted instead of on the next timer tick. `onNews` is called when one
 * arrives; the caller refetches. Returns whether the long poll is healthy, so
 * the caller can relax its timer while it is.
 *
 * A failure backs off (3 s doubling to 30 s), and an empty answer that comes
 * back suspiciously fast pauses too, so a broken route can never spin.
 */
export function useTalkLongPoll(
  token: string,
  newestId: number,
  onNews: () => void,
  asUser?: 'admin',
): boolean {
  const [healthy, setHealthy] = useState(false);
  const newestRef = useRef(newestId);
  const onNewsRef = useRef(onNews);
  useEffect(() => {
    newestRef.current = Math.max(newestRef.current, newestId);
    onNewsRef.current = onNews;
  });

  useEffect(() => {
    newestRef.current = newestId;
    if (!token) return undefined;
    const controller = new AbortController();
    const { signal } = controller;

    void (async () => {
      let backoff = BACKOFF_START_MS;
      while (!signal.aborted) {
        const after = newestRef.current;
        if (!after || document.hidden) {
          await sleep(HIDDEN_PAUSE_MS, signal);
          continue;
        }
        const startedAt = Date.now();
        try {
          const res = await api.waitTalkMessages(token, after, HOLD_S, asUser, signal);
          if (signal.aborted) break;
          if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'wait failed');
          backoff = BACKOFF_START_MS;
          setHealthy(true);
          const ids = detailList<{ id?: number }>(res, 'messages').map((m) => Number(m.id) || 0);
          const newest = Math.max(after, ...ids);
          if (newest > after) {
            newestRef.current = newest;
            onNewsRef.current();
          } else if (Date.now() - startedAt < QUICK_EMPTY_MS) {
            await sleep(QUICK_EMPTY_PAUSE_MS, signal);
          }
        } catch {
          if (signal.aborted) break;
          setHealthy(false);
          await sleep(backoff, signal);
          backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
        }
      }
    })();

    return () => {
      controller.abort();
      setHealthy(false);
    };
    // newestId seeds the loop once per conversation; later ids arrive through
    // the ref so a new message does not restart the open request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, asUser]);

  return healthy;
}

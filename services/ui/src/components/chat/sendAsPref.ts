// The admin "Send as" preference, remembered per browser. Kept apart from the
// SendAsSelector component so that file exports only a component (fast
// refresh needs that).
//
// Each surface keeps its own choice. Notes once shared the Talk chat's key, so
// an admin who posted to a channel as Admin then opened Notes as Admin too and
// saw the shared account's notes instead of their own.
import { useCallback, useState } from 'react';

export type SendAs = 'me' | 'admin';

/** Where the choice applies: the Talk chat, or the Nextcloud notes surfaces
 *  (the Notes page, Recipes and the Quick Notes widget). */
export type SendAsScope = 'talk' | 'notes';

const STORAGE_KEYS: Record<SendAsScope, string> = {
  talk: 'jarvis-talk-send-as',
  notes: 'jarvis-notes-open-as',
};

export function readSendAsPref(scope: SendAsScope = 'talk'): SendAs {
  try {
    return localStorage.getItem(STORAGE_KEYS[scope]) === 'admin' ? 'admin' : 'me';
  } catch {
    return 'me';
  }
}

export function useSendAsPref(
  isAdmin: boolean,
  scope: SendAsScope = 'talk',
): [SendAs, (next: SendAs) => void] {
  const [sendAs, setSendAs] = useState<SendAs>(() => readSendAsPref(scope));
  const set = useCallback(
    (next: SendAs) => {
      setSendAs(next);
      try {
        localStorage.setItem(STORAGE_KEYS[scope], next);
      } catch {
        // Private mode or blocked storage: the choice lasts for this page only.
      }
    },
    [scope],
  );
  return [isAdmin ? sendAs : 'me', set];
}

const ROOM_KEY = 'jarvis-talk-send-as-by-room';

function readRoomPrefs(): Record<string, SendAs> {
  try {
    const parsed = JSON.parse(localStorage.getItem(ROOM_KEY) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * The chat's choice, kept per conversation. One global choice meant picking
 * Admin for the family announcements room kept posting as Admin in every other
 * room too. A room nobody chose for is always "me".
 */
export function useConversationSendAs(
  isAdmin: boolean,
  token: string,
): [SendAs, (next: SendAs) => void] {
  const [prefs, setPrefs] = useState<Record<string, SendAs>>(readRoomPrefs);
  const set = useCallback(
    (next: SendAs) => {
      setPrefs((current) => {
        const updated = { ...current };
        if (next === 'admin') updated[token] = 'admin';
        else delete updated[token];
        try {
          localStorage.setItem(ROOM_KEY, JSON.stringify(updated));
        } catch {
          // Blocked storage: the choice lasts for this page only.
        }
        return updated;
      });
    },
    [token],
  );
  return [isAdmin && prefs[token] === 'admin' ? 'admin' : 'me', set];
}

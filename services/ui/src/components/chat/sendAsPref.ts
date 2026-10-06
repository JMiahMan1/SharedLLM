// The admin "Send as" preference, remembered per browser. Kept apart from the
// SendAsSelector component so that file exports only a component (fast
// refresh needs that).
import { useCallback, useState } from 'react';

export type SendAs = 'me' | 'admin';

const STORAGE_KEY = 'jarvis-talk-send-as';

export function readSendAsPref(): SendAs {
  if (typeof localStorage === 'undefined') return 'me';
  return localStorage.getItem(STORAGE_KEY) === 'admin' ? 'admin' : 'me';
}

export function useSendAsPref(isAdmin: boolean): [SendAs, (next: SendAs) => void] {
  const [sendAs, setSendAs] = useState<SendAs>(readSendAsPref);
  const set = useCallback((next: SendAs) => {
    setSendAs(next);
    if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, next);
  }, []);
  return [isAdmin ? sendAs : 'me', set];
}

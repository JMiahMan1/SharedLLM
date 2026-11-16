import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { App } from '@capacitor/app';
import { useNavigate } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { useAuth } from '../context/AuthContext';
import ChatNotify from '../plugins/chatNotify';
import NudgeOverlay, { type Nudge } from './chat/NudgeOverlay';

/**
 * Android glue that needs the router: once signed in, notifications are
 * switched on (asking for permission the first time), and a tapped
 * notification opens the page it is about.
 */
const NOTICE_ID = 'jarvis-notifications-off';
/** "Not now" quiets the notice on this device for a week (it covered the top of every screen). */
const SNOOZE_KEY = 'jarvis-notifications-off-snoozed-until';
const SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;
/** Shown long enough to read, then out of the way of the page's own buttons. */
const NOTICE_MS = 15_000;

function snoozed(): boolean {
  try {
    return Number(window.localStorage.getItem(SNOOZE_KEY) || 0) > Date.now();
  } catch {
    return false;
  }
}

function snooze(): void {
  try {
    window.localStorage.setItem(SNOOZE_KEY, String(Date.now() + SNOOZE_MS));
  } catch {
    // Without storage it simply shows again next time.
  }
}

/**
 * Say so on this device when Android is not letting Jarvis show notices (a
 * declined permission prompt, or family chat switched off): messages then
 * arrived and were silently dropped, which looked like Jarvis not sending them.
 */
async function warnIfNotificationsOff(): Promise<void> {
  if (typeof ChatNotify.notificationState !== 'function') return; // an older APK cannot tell
  let state: { app: boolean; chat: boolean };
  try {
    state = await ChatNotify.notificationState();
  } catch {
    return;
  }
  if (state.app && state.chat) {
    toast.dismiss(NOTICE_ID);
    return;
  }
  if (snoozed()) return;
  toast(
    (t) => (
      <span className="flex flex-wrap items-center gap-2" data-testid="notifications-off">
        <span className="min-w-0 flex-1">
          {state.app ? 'Family chat notifications are off on this device.' : 'Jarvis can’t show notifications on this device, so messages won’t pop up.'}
        </span>
        <button
          type="button"
          className="min-h-11 rounded-lg bg-sky-500/20 px-3 text-sm font-semibold text-sky-100"
          onClick={() => {
            toast.dismiss(t.id);
            void ChatNotify.openNotificationSettings?.().catch(() => {});
          }}
        >
          Turn on
        </button>
        <button
          type="button"
          className="min-h-11 rounded-lg px-3 text-sm font-semibold text-slate-300 hover:bg-white/10"
          data-testid="notifications-off-later"
          onClick={() => {
            snooze();
            toast.dismiss(t.id);
          }}
        >
          Not now
        </button>
      </span>
    ),
    { id: NOTICE_ID, duration: NOTICE_MS },
  );
}

export default function NativeBridge() {
  const navigate = useNavigate();
  const { token } = useAuth();
  const native = Capacitor.isNativePlatform();

  useEffect(() => {
    if (!native || !token) return;
    void ChatNotify.start()
      .then(() => warnIfNotificationsOff())
      .catch((err) => console.warn('[ChatNotify] start failed', err));
    // Coming back from Android's settings (or any time later): check again.
    const resumed = App.addListener('resume', () => void warnIfNotificationsOff());
    return () => {
      void resumed.then((h) => h.remove()).catch(() => {});
    };
  }, [native, token]);

  useEffect(() => {
    if (!native) return;
    const handle = ChatNotify.addListener('open', ({ path }) => {
      if (typeof path === 'string' && path.startsWith('/')) navigate(path);
    });
    return () => {
      void handle.then((h) => h.remove()).catch(() => {});
    };
  }, [native, navigate]);

  // A nudge while Jarvis is open: shown full screen, not as one more message.
  const [nudge, setNudge] = useState<Nudge | null>(null);
  useEffect(() => {
    if (!native) return;
    const handle = ChatNotify.addListener('nudge', ({ body, path }) => {
      setNudge({ body: body || 'You were nudged in your private chat', path: path && path.startsWith('/') ? path : '/family' });
    });
    return () => {
      void handle.then((h) => h.remove()).catch(() => {});
    };
  }, [native]);
  const closeNudge = useCallback(() => setNudge(null), []);

  if (!nudge) return null;
  return (
    <NudgeOverlay
      nudge={nudge}
      onClose={closeNudge}
      onOpen={() => {
        setNudge(null);
        navigate(nudge.path);
      }}
    />
  );
}

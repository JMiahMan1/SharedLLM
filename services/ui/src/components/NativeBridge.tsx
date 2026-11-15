import { useEffect } from 'react';
import toast from 'react-hot-toast';
import { App } from '@capacitor/app';
import { useNavigate } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { useAuth } from '../context/AuthContext';
import ChatNotify from '../plugins/chatNotify';

/**
 * Android glue that needs the router: once signed in, notifications are
 * switched on (asking for permission the first time), and a tapped
 * notification opens the page it is about.
 */
const NOTICE_ID = 'jarvis-notifications-off';

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
      </span>
    ),
    { id: NOTICE_ID, duration: Infinity },
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

  return null;
}

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { useAuth } from '../context/AuthContext';
import ChatNotify from '../plugins/chatNotify';

/**
 * Android glue that needs the router: once signed in, notifications are
 * switched on (asking for permission the first time), and a tapped
 * notification opens the page it is about.
 */
export default function NativeBridge() {
  const navigate = useNavigate();
  const { token } = useAuth();
  const native = Capacitor.isNativePlatform();

  useEffect(() => {
    if (!native || !token) return;
    void ChatNotify.start().catch((err) => console.warn('[ChatNotify] start failed', err));
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

/** Browser push subscription helpers (Web Push / VAPID). */
import { api } from '../services/api';
import { storageGetSync } from './storage';

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

export function permissionState(): NotificationPermission | 'unsupported' {
  if (!pushSupported()) return 'unsupported';
  return Notification.permission;
}

/**
 * Hand this person's API key to the push service worker (public/push-sw.js), which cannot read the
 * page's storage, so a notice's buttons answer `POST /api/push/action` as them, as the bell does.
 * Nothing happens until push is enabled on this browser (no registration, no worker).
 */
export async function sharePushKey(key: string | null = storageGetSync('jarvis_api_key')): Promise<boolean> {
  if (!pushSupported()) return false;
  const registration = await navigator.serviceWorker.getRegistration('/push-sw.js');
  const worker = registration?.active;
  if (!worker) return false;
  worker.postMessage({ type: 'jarvis-push-key', key });
  return true;
}

/** Subscribe this device and register it with the telemetry service. */
export async function enablePush(): Promise<{ ok: boolean; reason?: string }> {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' };
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return { ok: false, reason: permission };

  const { public_key: publicKey } = await api.getPushPublicKey();
  if (!publicKey) return { ok: false, reason: 'no-vapid-key' };

  const registration = await navigator.serviceWorker.register('/push-sw.js');
  await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
  });
  await api.subscribePush(subscription.toJSON());
  await sharePushKey();
  return { ok: true };
}

/** Unsubscribe this device and remove it server-side. */
export async function disablePush(): Promise<{ ok: boolean }> {
  if (!pushSupported()) return { ok: false };
  const registration = await navigator.serviceWorker.getRegistration('/push-sw.js');
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription) {
    await api.unsubscribePush(subscription.endpoint);
    await subscription.unsubscribe();
  }
  await sharePushKey(null);
  return { ok: true };
}

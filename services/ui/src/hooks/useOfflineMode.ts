import { Capacitor } from '@capacitor/core';
import { useSystemHealth } from './useSystemHealth';

/**
 * Whether the Android app cannot reach the Jarvis server (not merely "no
 * Wi-Fi": mobile data may still work). It reuses the header's health check:
 * offline means that check failed with no answer at all, not that the server
 * answered with a problem. Back online, the next check clears it by itself.
 */
export function isUnreachable(error: unknown): boolean {
  if (!error) return false;
  const e = error as { response?: unknown; code?: string };
  return !e.response;
}

export function useOfflineMode(): { offline: boolean; android: boolean } {
  const android = Capacitor.getPlatform() === 'android';
  const { error, isError, failureCount, failureReason, isSuccess } = useSystemHealth();
  // Offline as soon as a check went unanswered (not only after every retry), and back as soon as one answers.
  const unanswered = (isError && isUnreachable(error)) || (!isSuccess && failureCount > 0 && isUnreachable(failureReason));
  return { offline: android && unanswered, android };
}

/**
 * Debug logging for the media stack (BUG-70). Silent unless the person turns
 * it on with `localStorage.jarvis_debug_media = '1'`, so hot paths (position
 * ticks, socket events) never flood the console in normal use.
 */
export function mediaDebugEnabled(): boolean {
  try {
    return localStorage.getItem('jarvis_debug_media') === '1';
  } catch {
    return false;
  }
}

export function mediaLog(...args: unknown[]): void {
  if (mediaDebugEnabled()) console.debug('[media]', ...args);
}

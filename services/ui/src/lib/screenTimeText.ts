// How a screen-time lock is described to the member and their parents.
import type { ScreenTimeStatus } from '../types/api';

function untilText(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

export function lockedLine(status: ScreenTimeStatus): string {
  const until = untilText(status.until);
  switch (status.reason) {
    case 'paused':
      return until ? `Paused by a parent until ${until}.` : 'Paused by a parent for now.';
    case 'bedtime':
      return `Bedtime. The phone is back on at ${until}.`;
    case 'school':
      return `School time until ${until}.`;
    case 'limit':
      return "That's today's time. It resets at midnight.";
    default:
      return '';
  }
}

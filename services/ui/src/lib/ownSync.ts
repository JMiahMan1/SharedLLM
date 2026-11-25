import type { OwnSyncCategory, OwnSyncPeer, OwnSyncTypeStatus } from '../plugins/ownSync';

/** The kinds of data, in the order the diagnostics list them, with the words shown. */
export const OWN_SYNC_CATEGORIES: Array<{ id: OwnSyncCategory; label: string }> = [
  { id: 'device', label: 'Location & battery' },
  { id: 'steps', label: 'Steps' },
  { id: 'watch', label: 'Jarvis Watch' },
  { id: 'rooms', label: 'Chat rooms' },
  { id: 'family', label: 'Family information' },
  { id: 'chat', label: 'Offline chat' },
  { id: 'requests', label: 'Jarvis requests' },
  { id: 'guardian', label: 'Parent controls' },
];

/** "Nov 25, 3:42 PM": the same format as the rest of the diagnostics. */
export function shortWhen(ms: number | null | undefined): string {
  if (!ms) return 'never';
  return new Date(ms).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** "In sync", or "Updated from Galaxy Tab at Nov 25, 3:42 PM". */
export function statusText(status: OwnSyncTypeStatus | undefined): string {
  if (!status) return 'Not synced yet';
  if (status.status === 'updated') return `Updated from ${status.from || 'your other device'} at ${shortWhen(status.at)}`;
  return 'In sync';
}

/** A device's name for people, never its id. */
export function peerName(peer: OwnSyncPeer): string {
  return peer.name?.trim() || 'Your other device';
}

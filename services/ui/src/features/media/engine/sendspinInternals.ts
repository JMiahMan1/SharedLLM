/**
 * The only place that reaches into @sendspin/sendspin-js private fields.
 *
 * sendspin-js (pinned at 3.2.0) offers no public way to get the WebSocket it
 * adopted or to resume its Web Audio context from a user gesture, and the web
 * player needs both. Every such access lives here, so a library upgrade that
 * renames them breaks one file and one test (`sendspinInternals.test.ts`
 * checks the shape against the real library), not the player.
 */

interface SendspinPrivate {
  core?: { wsManager?: { ws?: WebSocket | null } };
  scheduler?: { resumeAudioContext?: () => Promise<void>; audioContext?: AudioContext | null };
}

const priv = (player: unknown): SendspinPrivate => (player ?? {}) as SendspinPrivate;

/** The socket sendspin-js is actually using (it may replace the one it was given). */
export function sendspinSocket(player: unknown): WebSocket | null {
  return priv(player).core?.wsManager?.ws ?? null;
}

/** Resume Sendspin's AudioContext; call from inside a user gesture. */
export function resumeSendspinAudio(player: unknown): void {
  const sched = priv(player).scheduler;
  if (!sched) return;
  if (sched.resumeAudioContext) void sched.resumeAudioContext().catch(() => {});
  else if (sched.audioContext?.state === 'suspended') void sched.audioContext.resume().catch(() => {});
}

/** The private fields this file relies on that a player instance lacks. */
export function missingSendspinInternals(player: unknown): string[] {
  const p = priv(player);
  const missing: string[] = [];
  if (!p.core || typeof p.core !== 'object') missing.push('core');
  else if (!p.core.wsManager || typeof p.core.wsManager !== 'object') missing.push('core.wsManager');
  else if (!('ws' in p.core.wsManager)) missing.push('core.wsManager.ws');
  if (!p.scheduler || typeof p.scheduler !== 'object') missing.push('scheduler');
  else if (typeof p.scheduler.resumeAudioContext !== 'function') missing.push('scheduler.resumeAudioContext');
  return missing;
}

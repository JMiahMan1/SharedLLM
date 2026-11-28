/**
 * How long a take is, in the shape a voice note is read: "0:42", or "1:05".
 *
 * Its own module because two files want it and a `.tsx` that exports a helper
 * as well as a component breaks fast refresh.
 */
export function audioLength(seconds?: number): string {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return '';
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

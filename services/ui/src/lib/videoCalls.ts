import type { GuardianConcern, ScreenRules, VideoCallRules } from '../types/api';

/**
 * A child's whole rules with their video-call choices changed: the rules PUT
 * replaces everything, so the rest is carried over (the pause is the server's
 * own and never sent back). A key set to undefined is left out, which means
 * the default again (a list reset to the shipped one).
 */
export function withVideoCalls(rules: ScreenRules | undefined, patch: Partial<Record<keyof VideoCallRules, unknown>>): ScreenRules {
  const merged: Record<string, unknown> = { ...(rules?.video_calls || {}) };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) delete merged[key];
    else merged[key] = value;
  }
  const full: ScreenRules = { ...(rules || {}), video_calls: merged as VideoCallRules };
  delete full.paused;
  delete full.paused_until;
  return full;
}

/** A video-call frame's concern: where and when, in words ("Video call · Chrome (facetime.apple.com) · 3:41 PM"). */
export function videoCallLine(concern: GuardianConcern): string | null {
  if (concern.feature !== 'video_call') return null;
  const call = concern.video_call || {};
  const where = call.label || concern.item || 'a video call';
  const at = call.call_started_at || concern.at;
  const when = at ? new Date(at) : null;
  const time = when && !Number.isNaN(when.getTime())
    ? when.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : '';
  return ['Video call', where, time].filter(Boolean).join(' · ');
}

/** Whether a video-call flag was sent to the parents, in their words. */
export function videoCallAlertText(concern: GuardianConcern): string | null {
  const alert = concern.alert;
  if (concern.feature !== 'video_call' || !alert) return null;
  if (alert.state === 'unsure') return 'Unsure, not sent';
  if (alert.state === 'sent') return `Checked twice on ${alert.confirmed_frames} frames · parents notified`;
  return 'Checked twice · already notified for this call';
}

/** Why a phone cannot take video call screenshots, or null when it can (or has not said). */
export function captureProblem(capture?: string, last?: string): string | null {
  if (capture && capture !== 'ready') {
    if (capture === 'guard_off') return 'The screen guard is off, so video calls are not checked.';
    if (capture === 'android_too_old') return 'This device runs Android 10 or older: video call checks need Android 11.';
    if (capture === 'no_screenshot_capability') {
      return 'The screen guard has not been allowed to take screenshots yet. Switch the guard off and on in Accessibility settings after the app update.';
    }
    return `Video call checks are not available here (${capture}).`;
  }
  if (last === 'blank_screen') return 'The last call app hid its screen from screenshots, so that call could not be checked.';
  if (last && last.startsWith('screenshot_failed')) return `The last video call screenshot failed (${last.replace('screenshot_failed_', 'error ')}).`;
  if (last && last.startsWith('upload_failed')) return 'The last video call screenshot could not be sent to Jarvis.';
  return null;
}

/** One entry per line or comma, trimmed, without blanks or repeats. */
export function parseList(text: string): string[] {
  return Array.from(new Set(text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean)));
}

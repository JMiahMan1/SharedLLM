/**
 * How a sensor failure should be classified, and therefore whether it may
 * switch the sensor off for good.
 *
 * ## Why this exists
 *
 * `LocationContext` used to decide "the user denied permission" by scanning an
 * error *message* for the substring "permission" (or "denied"). That turned any
 * transient failure whose text happened to mention permissions — a plugin that
 * was not ready yet, a backgrounded watch, an Android WebView timing out — into
 * a permanent, persisted disable. The user then had to hunt through Settings to
 * turn tracking back on, and neither Wander nor Health said why their data had
 * gone stale. Worse, `StepCounter.startPolling()` had *no* classification at
 * all: any throw disabled steps forever.
 *
 * The rule this module encodes:
 *
 *   **Only an explicit, structured denial may switch a sensor off. Everything
 *   else is transient and must be retried.**
 *
 * That is the difference between an app that heals itself and one that quietly
 * dies. Failing toward "still on" costs a retry; failing toward "off" costs the
 * user their tracking with no visible explanation.
 *
 * ## Why a thrown step error is *never* a denial
 *
 * The native step plugin reports permission only through the *return value* of
 * `requestPermission()` (`{ granted, permanentlyDenied }`) and through
 * `isAvailable()`. It puts no code on a thrown error. So there is nothing in a
 * step-plugin exception that distinguishes "the user said no" from "the bridge
 * was busy". Classifying one as the other is exactly the bug being fixed, so
 * {@link classifyStepFailure} never returns `'denied'` — a caller that wants a
 * denial must check the structured result itself.
 */

/**
 * - `'denied'` — the user or the OS explicitly refused. Only this may persist a
 *   disable; the message should name the setting to change.
 * - `'unsupported'` — the hardware or platform cannot provide this at all
 *   (no pedometer, browser without geolocation). Retrying will never help, but
 *   the sensor should report `'unavailable'`, not `'denied'`, so the UI can say
 *   "this device can't" instead of "you said no".
 * - `'transient'` — anything else. Retry with backoff; never persist a disable.
 */
export type SensorFailureKind = 'denied' | 'unsupported' | 'transient';

/** Geolocation's PERMISSION_DENIED. Identical on the web and Capacitor APIs. */
const PERMISSION_DENIED = 1;

/**
 * Decide whether a *location* failure may disable location tracking.
 *
 * The only denial signal honoured is the numeric `code === 1` that both
 * `navigator.geolocation` and `@capacitor/geolocation` report. Anything else —
 * including an `Error` whose text says "permission denied" — is transient,
 * because a real OS denial always arrives as a coded error.
 */
export function classifyLocationFailure(err: unknown): SensorFailureKind {
  if (typeof err === 'object' && err !== null && 'code' in err) {
    const code = (err as { code?: unknown }).code;
    if (code === PERMISSION_DENIED) return 'denied';
    // POSITION_UNAVAILABLE (2) and TIMEOUT (3) are routine indoors/background.
    if (code === 2 || code === 3) return 'transient';
  }
  return 'transient';
}

/**
 * Decide whether a *step counter* failure may disable step tracking.
 *
 * Always `'transient'`. The native plugin exposes no denial code on a thrown
 * error (see the module docstring), so there is no sound way to read a denial
 * out of an exception. Use {@link isStepPermissionDenied} against the
 * structured `requestPermission()` result instead.
 */
export function classifyStepFailure(err: unknown): SensorFailureKind {
  // The parameter is part of the signature (callers pass the same throwable they
  // log) but is deliberately unread: see the docstring above.
  void err;
  return 'transient';
}

/** True when the structured `requestPermission()` result means "refused". */
export function isStepPermissionDenied(result: { granted: boolean } | null | undefined): boolean {
  return Boolean(result) && result?.granted === false;
}

/**
 * True when the platform reported the sensor cannot work here at all, so the UI
 * should say "unavailable" rather than "denied" and stop offering a retry.
 */
export function isUnsupportedAvailability(
  availability: { available: boolean } | null | undefined,
): boolean {
  return Boolean(availability) && availability?.available === false;
}

/** Best-effort human-readable text for an unknown throwable. */
export function sensorErrorMessage(err: unknown, fallback: string): string {
  if (typeof err === 'string' && err.trim()) return err.trim();
  if (err instanceof Error && err.message.trim()) return err.message.trim();
  if (typeof err === 'object' && err !== null) {
    const message = (err as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message.trim();
  }
  return fallback;
}

const RETRY_BASE_MS = 5_000;
const RETRY_MAX_MS = 5 * 60_000;

/**
 * Exponential backoff for retrying a transient sensor failure.
 *
 * `attempt` is zero-based. Capped so a long outage settles at one attempt every
 * five minutes rather than drifting into never-again.
 */
export function nextRetryDelayMs(attempt: number): number {
  const step = Math.max(0, Math.floor(attempt));
  const exponential = RETRY_BASE_MS * 2 ** Math.min(step, 20);
  return Math.min(exponential, RETRY_MAX_MS);
}

/**
 * Whether a sensor that has been failing should be retried now.
 *
 * `'denied'` and `'unsupported'` are terminal by design: the first needs the
 * user to change an OS setting, the second cannot be fixed in software. Only a
 * transient failure is worth another attempt.
 */
export function shouldRetry(kind: SensorFailureKind): boolean {
  return kind === 'transient';
}
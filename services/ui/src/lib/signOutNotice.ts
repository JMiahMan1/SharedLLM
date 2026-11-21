/**
 * Why this device was signed out, so the login page can say so.
 *
 * A server that refuses the stored key (an admin's "sign everyone out and
 * reset keys", or a key revoked some other way) sends the person to /login.
 * Landing there with no word of why reads like a bug, so the reason is kept
 * here until the next successful sign-in. Only a reason code is stored, never
 * a key or anything about the account.
 */
import axios from 'axios';

export const SIGNOUT_REASON_KEY = 'jarvis_signout_reason';

/** The 401 body code identity and the gateway send for a key an admin reset. */
export const SIGN_IN_RESET_CODE = 'sign_in_reset';

/** The 401 body code identity and the gateway send when they refuse the presented key itself. */
export const API_KEY_INVALID_CODE = 'api_key_invalid';

/**
 * The only codes that mean the device's own key was refused. Any other 401 (a
 * route passing an upstream service's 401 through, a call made without a key)
 * says nothing about the stored key and must never sign anyone out.
 */
const KEY_REFUSAL_CODES: ReadonlySet<string> = new Set([SIGN_IN_RESET_CODE, API_KEY_INVALID_CODE]);

export type SignOutReason = 'sign_in_reset' | 'session_ended';

export const SIGN_IN_RESET_MESSAGE = 'Your sign-in was reset, please sign in again';
export const SESSION_ENDED_MESSAGE = 'Your session ended. Please sign in again.';

const REASONS: ReadonlySet<string> = new Set<SignOutReason>(['sign_in_reset', 'session_ended']);

function statusOf(error: unknown): number | undefined {
  if (axios.isAxiosError(error)) return error.response?.status;
  const status = (error as { response?: { status?: unknown } } | null)?.response?.status;
  return typeof status === 'number' ? status : undefined;
}

function bodyOf(error: unknown): unknown {
  return (error as { response?: { data?: unknown } } | null)?.response?.data;
}

/** A 401 whose body says the sign-in was reset by an admin. */
export function isSignInReset(error: unknown): boolean {
  if (statusOf(error) !== 401) return false;
  const body = bodyOf(error) as { code?: unknown } | null | undefined;
  return body?.code === SIGN_IN_RESET_CODE;
}

/** A 401 in which identity or the gateway refused the presented key itself. */
export function isKeyRefusal(error: unknown): boolean {
  if (statusOf(error) !== 401) return false;
  const code = (bodyOf(error) as { code?: unknown } | null | undefined)?.code;
  return typeof code === 'string' && KEY_REFUSAL_CODES.has(code);
}

/** Whether a request carried a key (an Authorization header). */
export function carriedKey(error: unknown): boolean {
  const headers = (error as { config?: { headers?: Record<string, unknown> | { get?: (n: string) => unknown } } } | null)
    ?.config?.headers;
  if (!headers) return false;
  const viaGet = typeof (headers as { get?: unknown }).get === 'function'
    ? (headers as { get: (n: string) => unknown }).get('Authorization')
    : undefined;
  const value = viaGet ?? (headers as Record<string, unknown>).Authorization ?? (headers as Record<string, unknown>).authorization;
  return typeof value === 'string' && value.trim().length > 0;
}

/** The reason to record for a refused stored key. */
export function signOutReasonFor(error: unknown): SignOutReason {
  return isSignInReset(error) ? 'sign_in_reset' : 'session_ended';
}

export function signOutMessage(reason: SignOutReason): string {
  return reason === 'sign_in_reset' ? SIGN_IN_RESET_MESSAGE : SESSION_ENDED_MESSAGE;
}

export function recordSignOutReason(reason: SignOutReason): void {
  try {
    localStorage.setItem(SIGNOUT_REASON_KEY, reason);
  } catch (err) {
    // Storage can be full or blocked; the person still lands on /login, and the
    // console says why the message is missing.
    console.error('[auth] could not record why this device was signed out:', err);
  }
}

export function readSignOutReason(): SignOutReason | null {
  try {
    const value = localStorage.getItem(SIGNOUT_REASON_KEY);
    return value && REASONS.has(value) ? (value as SignOutReason) : null;
  } catch {
    return null;
  }
}

export function clearSignOutReason(): void {
  try {
    localStorage.removeItem(SIGNOUT_REASON_KEY);
  } catch (err) {
    console.error('[auth] could not clear the sign-out notice:', err);
  }
}

/** The message the login page shows, or null when nobody was signed out. */
export function readSignOutMessage(): string | null {
  const reason = readSignOutReason();
  return reason ? signOutMessage(reason) : null;
}

/**
 * Which Music Assistant a person uses, as identity reports it
 * (GET /api/auth/music-assistant/status; services/identity/music_assistant.py).
 *
 * Their own server when they saved a URL; otherwise the household's, which is
 * wherever HomeHub's Music Assistant integration points. On the household
 * server each person has their own Music Assistant account (Jarvis makes one).
 * Nothing switches a person to another server by itself: an own server that
 * does not answer is shown as an error with an explicit way back.
 */
export type MusicAssistantState = 'ok' | 'unreachable' | 'token_refused' | 'not_set_up';

export interface MusicAssistantStatus {
  server: 'own' | 'household' | 'absent';
  login: 'own' | 'provisioned' | 'granted' | 'absent';
  url: string | null;
  own_url: string | null;
  household_url: string | null;
  household_error: string | null;
  can_use_household: boolean;
  note: string;
  ma_username: string | null;
  state: MusicAssistantState;
  message: string;
}

/** The card's one line. */
export function musicAssistantLine(status: MusicAssistantStatus | null | undefined, loading = false): string {
  if (!status) return loading ? 'Checking...' : 'Status unavailable';
  if (status.state === 'ok') {
    if (status.server === 'own') return 'Your own server';
    return status.login === 'granted' ? 'Household server, shared login' : 'Household server, your account';
  }
  if (status.state === 'unreachable') return status.server === 'own' ? 'Your server is not answering' : 'Not answering';
  if (status.state === 'token_refused') return 'Token refused';
  return 'Not set up';
}

/** Whether the card shows the problem as an error (red) rather than plain information. */
export function musicAssistantIsError(status: MusicAssistantStatus | null | undefined): boolean {
  return !!status && status.state !== 'ok';
}

/** Show "Use the household Music Assistant": they have their own URL and the household has a server. */
export function offersHousehold(status: MusicAssistantStatus | null | undefined): boolean {
  return !!status && status.can_use_household;
}

/** Offer a fresh login on the household server when its token is refused (MA tokens expire after a year). */
export function offersNewLogin(status: MusicAssistantStatus | null | undefined): boolean {
  return !!status && status.server === 'household' && status.state === 'token_refused' && status.login !== 'granted';
}

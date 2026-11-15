// The private chat's shapes and the small rules its screens follow, kept free
// of React so they can be tested directly.
import { isMine, type TalkMessage } from './chatModel';

export interface PrivateChatInfo {
  token: string;
  members: string[];
  partner: string;
  partner_name: string;
  created_by: string;
  created_at: number;
  kdf: { name: 'PBKDF2-SHA256'; salt: string; iterations: number };
  verifier: { iv: string; ct: string };
}

export interface PrivateChatStatus {
  /** A parent (father or mother) with a chat account. */
  eligible: boolean;
  reason?: string | null;
  chats: PrivateChatInfo[];
  /** The other parents a chat could be started with. */
  partners: Array<{ username: string; display_name: string }>;
  min_iterations: number;
}

/** The shortest password the create form accepts. */
export const MIN_PASSWORD_LENGTH = 8;

/** Why a new password cannot be used yet, or null when it can. */
export function passwordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  if (password !== confirm) return 'The two passwords do not match.';
  return null;
}

/**
 * Whether a nudge makes sense: the latest message in the chat is mine and the
 * other parent has not read it (Talk's everyone-has-read marker is behind it).
 */
export function canNudge(messages: TalkMessage[], myActorIds: ReadonlySet<string>, lastCommonRead?: number): boolean {
  const sent = messages.filter((m) => typeof m.id === 'number' && !m.deleted && !m.system_message);
  const latest = sent[sent.length - 1];
  if (!latest || !isMine(latest, myActorIds)) return false;
  return (lastCommonRead ?? 0) < Number(latest.id);
}

/** The server's reason from an axios error (detail), else the error's own message. */
export function errorText(error: unknown, fallback: string): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data && typeof data === 'object' && typeof (data as { detail?: unknown }).detail === 'string') {
    return (data as { detail: string }).detail;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

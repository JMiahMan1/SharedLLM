/**
 * Who sees Beloved's entry point: only a father or a mother, never a minor or
 * the household account, who is one of the two members of a private chat the
 * server says is one father and one mother. An administrator who is not one of
 * the couple never sees it. The server answers everyone else 404 regardless
 * (services/gateway/beloved.py); this keeps the button itself out of sight.
 */
export const COUPLE_ROLES: ReadonlySet<string> = new Set(['father', 'mother']);

export interface Viewer {
  username?: string | null;
  family_role?: string | null;
  is_minor?: boolean | null;
  is_system_default?: boolean | null;
}

export interface ChatForBeloved {
  members?: readonly string[];
  beloved?: boolean;
}

export function canSeeBeloved(viewer: Viewer | null | undefined, chat: ChatForBeloved | null | undefined): boolean {
  if (!viewer?.username || !chat) return false;
  if (!COUPLE_ROLES.has(String(viewer.family_role ?? ''))) return false;
  if (viewer.is_minor || viewer.is_system_default) return false;
  return chat.beloved === true && Array.isArray(chat.members) && chat.members.includes(viewer.username);
}

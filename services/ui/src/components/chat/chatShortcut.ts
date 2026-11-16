// What a conversation's home-screen shortcut carries, kept free of React and
// of the plugin so it can be tested directly. A shortcut carries a name, a
// picture and a page: never a message, and never anything that unlocks a
// private chat (its page is the chat, which opens on the lock screen).
import type { ChatShortcutRequest } from '../../plugins/chatShortcut';
import { avatarTint, initials, type TalkConversation } from './chatModel';
import { findFamilyMember, nameColour, type FamilyLookup } from './familyDirectory';
import type { PrivateChatInfo } from './privateChatModel';

/** What a removed shortcut says when it is tapped. */
export const CHAT_SHORTCUT_GONE = 'This chat is no longer available';

export function chatShortcutId(token: string): string {
  return `chat:${token}`;
}

/** The page a notification tap opens for the same conversation. */
export function chatShortcutPath(token: string): string {
  return `/family?chat=${encodeURIComponent(token)}`;
}

export interface ChatShortcutTarget {
  token: string;
  label: string;
  /** The person whose picture it shows (a one-to-one or the private chat), else null. */
  username: string | null;
  /** Their `avatar` value ("upload:<v>", "preset:<id>" or null). */
  avatar: string | null;
  color: string;
  monogram: string;
}

/**
 * The shortcut a conversation would get, or null when it is not offered:
 * a private chat only for someone allowed the private chat, and only once
 * its record (who the other parent is) has loaded.
 */
export function chatShortcutTarget({
  conversation,
  people,
  privateInfo,
  mayUsePrivate,
}: {
  conversation: Pick<TalkConversation, 'token' | 'display_name' | 'type' | 'private' | 'name'> | undefined;
  people: FamilyLookup;
  privateInfo?: Pick<PrivateChatInfo, 'token' | 'partner' | 'partner_name'>;
  mayUsePrivate: boolean;
}): ChatShortcutTarget | null {
  if (!conversation?.token) return null;
  const token = conversation.token;
  const person = (id?: string | null, name?: string | null, fallback = 'Chat') => {
    const member = findFamilyMember(people, id, name);
    const label = (member?.display_name || name || id || '').trim() || fallback;
    return {
      token,
      label,
      username: member?.username ?? id ?? null,
      avatar: member?.avatar ?? null,
      color: avatarTint(label),
      monogram: initials(label),
    };
  };
  if (conversation.private) {
    if (!mayUsePrivate || !privateInfo || privateInfo.token !== token) return null;
    return person(privateInfo.partner, privateInfo.partner_name, 'Private chat');
  }
  if (conversation.type === 1) return person(conversation.name, conversation.display_name);
  const label = (conversation.display_name || '').trim() || 'Chat';
  return { token, label, username: null, avatar: null, color: nameColour(label), monogram: initials(label) };
}

/** What the plugin is sent: id, label, page, colour and monogram, and the picture when there is one. */
export function chatShortcutRequest(target: ChatShortcutTarget, icon?: string | null): ChatShortcutRequest {
  const request: ChatShortcutRequest = {
    id: chatShortcutId(target.token),
    label: target.label,
    path: chatShortcutPath(target.token),
    color: target.color,
    monogram: target.monogram,
  };
  if (icon) request.icon = icon;
  return request;
}

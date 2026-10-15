import { Sparkles, Users } from 'lucide-react';
import UserAvatar from '../UserAvatar';
import type { FamilyLookup } from './familyDirectory';
import { nameColour } from './familyDirectory';
import type { TalkConversation } from './chatModel';

/**
 * A conversation's face: the other person's own picture for a direct
 * message, Jarvis's spark for Jarvis, and a coloured group badge for a group.
 */
export default function ConversationAvatar({
  conversation,
  people,
  size = 48,
}: {
  conversation: Pick<TalkConversation, 'display_name' | 'type'> & { name?: string };
  people: FamilyLookup;
  size?: number;
}) {
  const name = conversation.display_name || 'Chat';
  if (conversation.name?.toLowerCase() === 'jarvis' || name.toLowerCase() === 'jarvis') {
    return (
      <span
        aria-hidden
        style={{ width: size, height: size }}
        className="flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-fuchsia-500 to-indigo-600 text-white shadow-lg shadow-fuchsia-500/20"
      >
        <Sparkles size={size * 0.45} />
      </span>
    );
  }
  if (conversation.type === 1) {
    const person = people(conversation.name);
    return <UserAvatar username={person?.username ?? conversation.name ?? name} avatar={person?.avatar} name={person?.display_name ?? name} size={size} />;
  }
  const colour = nameColour(name);
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, background: `linear-gradient(135deg, ${colour}, #4c1d95)` }}
      className="relative flex shrink-0 items-center justify-center rounded-full text-white shadow-lg"
    >
      <Users size={size * 0.42} />
    </span>
  );
}

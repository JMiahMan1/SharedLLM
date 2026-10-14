import { useState } from 'react';
import { Loader2, MessageCircle, Users, X } from 'lucide-react';
import PeoplePicker from './PeoplePicker';
import Sheet from './Sheet';
import type { TalkPerson } from './chatModel';

/**
 * Start a conversation the way a phone does: pick one person for a direct
 * message, or several and give the group a name.
 */
export default function NewChatSheet({
  me,
  busy,
  onDirect,
  onGroup,
  onClose,
}: {
  me: string[];
  busy: boolean;
  onDirect: (person: TalkPerson) => void;
  onGroup: (name: string, people: TalkPerson[]) => void;
  onClose: () => void;
}) {
  const [people, setPeople] = useState<TalkPerson[]>([]);
  const [name, setName] = useState('');
  const toggle = (p: TalkPerson) =>
    setPeople((current) => (current.some((c) => c.id === p.id) ? current.filter((c) => c.id !== p.id) : [...current, p]));
  const group = people.length > 1;
  const defaultName = people.map((p) => p.label.split(' ')[0]).join(', ');

  return (
    <Sheet title="New chat" onClose={onClose} testId="new-chat-sheet">
      {people.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {people.map((p) => (
            <button key={p.id} type="button" onClick={() => toggle(p)} className="flex items-center gap-1 rounded-full bg-purple-500/20 px-2.5 py-1 text-xs text-purple-100">
              {p.label} <X size={11} />
            </button>
          ))}
        </div>
      )}
      {group && (
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={`Group name (e.g. ${defaultName})`}
          aria-label="Group name"
          className="glass-input mb-3 w-full text-base sm:text-sm"
        />
      )}
      <PeoplePicker selected={people} onToggle={toggle} exclude={me} autoFocus />
      <button
        type="button"
        disabled={busy || people.length === 0}
        onClick={() => (group ? onGroup(name.trim() || defaultName, people) : onDirect(people[0]))}
        data-testid="new-chat-start"
        className="mt-3 w-full min-h-12 rounded-xl bg-purple-600 text-sm font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-50"
      >
        {busy ? <Loader2 size={15} className="animate-spin" /> : group ? <Users size={15} /> : <MessageCircle size={15} />}
        {group ? `Create group of ${people.length + 1}` : people.length ? `Message ${people[0].label}` : 'Pick someone'}
      </button>
    </Sheet>
  );
}

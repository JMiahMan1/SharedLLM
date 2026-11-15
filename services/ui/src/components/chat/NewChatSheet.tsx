import { useState } from 'react';
import { ChevronRight, Loader2, Lock, MessageCircle, Users, X } from 'lucide-react';
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
  onPrivate,
}: {
  me: string[];
  busy: boolean;
  onDirect: (person: TalkPerson) => void;
  onGroup: (name: string, people: TalkPerson[]) => void;
  onClose: () => void;
  /** Offered to a parent who has no private chat yet: start the encrypted one. */
  onPrivate?: () => void;
}) {
  const [people, setPeople] = useState<TalkPerson[]>([]);
  const [name, setName] = useState('');
  const toggle = (p: TalkPerson) =>
    setPeople((current) => (current.some((c) => c.id === p.id) ? current.filter((c) => c.id !== p.id) : [...current, p]));
  const group = people.length > 1;
  const defaultName = people.map((p) => p.label.split(' ')[0]).join(', ');

  return (
    <Sheet title="New chat" onClose={onClose} testId="new-chat-sheet">
      {onPrivate && (
        <button
          type="button"
          onClick={onPrivate}
          data-testid="new-private-chat-open"
          className="mb-3 flex w-full min-h-14 items-center gap-3 rounded-xl border border-rose-300/20 bg-rose-500/10 px-3 text-left"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-rose-500/20 text-rose-200">
            <Lock size={16} aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-white">Private chat</span>
            <span className="block text-xs text-slate-300">Just the two parents, end to end encrypted</span>
          </span>
          <ChevronRight size={16} className="shrink-0 text-slate-400" aria-hidden />
        </button>
      )}
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

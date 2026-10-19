import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Loader2, Search } from 'lucide-react';
import { api } from '../../services/api';
import UserAvatar from '../UserAvatar';
import { findFamilyMember, useFamilyDirectory } from './familyDirectory';
import { detailList, type TalkPerson } from './chatModel';

/**
 * Find family members by name. Used to start a chat, to make a group and to
 * add someone to a conversation, so all three search the same way.
 */
export default function PeoplePicker({
  selected,
  onToggle,
  exclude = [],
  token,
  autoFocus = false,
}: {
  selected: TalkPerson[];
  onToggle: (person: TalkPerson) => void;
  /** Ids not to offer (people already in the conversation, yourself). */
  exclude?: string[];
  token?: string;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const directory = useFamilyDirectory();
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const { data, isFetching } = useQuery({
    queryKey: ['talk-people', debounced, token ?? ''],
    queryFn: () => api.searchTalkPeople(debounced, token),
    select: (r) => detailList<TalkPerson>(r, 'people'),
    staleTime: 60_000,
  });
  const chosen = new Set(selected.map((p) => p.id));
  const hidden = new Set(exclude.map((id) => id.toLowerCase()));
  const people = (data ?? []).filter((p) => !hidden.has(p.id.toLowerCase()));

  return (
    <div className="space-y-2" data-testid="people-picker">
      <label className="relative block">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          autoFocus={autoFocus}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search people"
          aria-label="Search people"
          className="glass-input w-full rounded-full pl-8 text-base sm:text-sm"
        />
        {isFetching && <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-500" />}
      </label>
      <ul className="max-h-64 overflow-y-auto" aria-label="People">
        {people.map((person) => {
          const on = chosen.has(person.id);
          const member = findFamilyMember(directory, person.id, person.label);
          return (
            <li key={person.id}>
              <button
                type="button"
                onClick={() => onToggle(person)}
                aria-pressed={on}
                className="flex w-full min-h-12 items-center gap-3 rounded-xl px-2 text-left hover:bg-white/5"
              >
                <UserAvatar
                  username={member?.username ?? person.id}
                  avatar={member?.avatar}
                  name={member?.display_name ?? person.label}
                  size={36}
                />
                <span className="min-w-0 flex-1 truncate text-sm text-slate-100">{person.label}</span>
                <span
                  className={`flex h-6 w-6 items-center justify-center rounded-full border ${on ? 'border-purple-400 bg-purple-500 text-white' : 'border-white/20'}`}
                >
                  {on && <Check size={13} />}
                </span>
              </button>
            </li>
          );
        })}
        {!isFetching && people.length === 0 && <li className="px-2 py-4 text-sm text-slate-500">No one found.</li>}
      </ul>
    </div>
  );
}

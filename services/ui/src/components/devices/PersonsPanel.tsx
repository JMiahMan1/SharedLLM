import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Pencil, Plus, Trash2, Users } from 'lucide-react';
import { api } from '../../services/api';
import { failureText } from '../../lib/discoveredDevices';
import { createPerson, deletePerson, getPersons, toggled, updatePerson, type Person, type PersonBody, type Tracker } from '../../lib/homeSetup';
import { confirmDialog } from '../ui/confirmDialog';
import SetupSection from './SetupSection';
import { BTN, DANGER, INPUT } from './setupStyles';

const PERSONS = ['homehub-persons'];

/** Name, the Jarvis user it is, and the trackers (phones, tags) whose location is theirs. */
function PersonEditor({ initial, trackers, taken, users, submitLabel, busy, onSubmit, onCancel }: {
  initial: PersonBody; trackers: Tracker[]; taken: Set<string>; users: string[]; submitLabel: string; busy: boolean;
  onSubmit: (body: PersonBody) => void; onCancel?: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [user, setUser] = useState(initial.jarvis_user || '');
  const [picked, setPicked] = useState(initial.device_trackers);
  const listId = useId();
  return (
    <form className="space-y-2" data-testid="person-editor" onSubmit={(e) => {
      e.preventDefault();
      if (name.trim()) onSubmit({ name: name.trim(), jarvis_user: user.trim().toLowerCase() || null, device_trackers: picked });
    }}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="text-xs text-slate-400">Name
          <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="text-xs text-slate-400">Jarvis user
          <input className={INPUT} value={user} list={listId} placeholder="username (optional)" autoCapitalize="none"
            onChange={(e) => setUser(e.target.value)} />
          <datalist id={listId}>{users.map((u) => <option key={u} value={u} />)}</datalist>
        </label>
      </div>
      <fieldset>
        <legend className="text-xs text-slate-400">Trackers</legend>
        {trackers.length === 0 && <p className="text-xs text-slate-500">No trackers yet. A phone adds one when it registers.</p>}
        <div className="mt-1 flex flex-wrap gap-1.5">
          {trackers.map((t) => {
            const elsewhere = taken.has(t.entity_id) && !picked.includes(t.entity_id);
            return (
              <label key={t.entity_id} className={`inline-flex min-h-11 max-w-full items-center gap-2 rounded-lg border border-white/10 px-3 text-xs ${
                elsewhere ? 'opacity-50' : 'text-slate-200'}`}>
                <input type="checkbox" className="h-11 w-5 shrink-0" checked={picked.includes(t.entity_id)} disabled={elsewhere}
                  onChange={() => setPicked((p) => toggled(p, t.entity_id))} />
                <span className="min-w-0 truncate">{t.name}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={BTN} disabled={busy || !name.trim()}><Plus size={14} /> {submitLabel}</button>
        {onCancel && <button type="button" className={BTN} onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

function PersonRow({ person, trackers, taken, users, onChanged }: {
  person: Person; trackers: Tracker[]; taken: Set<string>; users: string[]; onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const save = useMutation({
    mutationFn: (body: PersonBody) => updatePerson(person.id, body),
    onSuccess: () => { setEditing(false); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: () => deletePerson(person.id),
    onSuccess: () => { toast.success(`${person.name} was removed.`); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const names = person.device_trackers.map((id) => trackers.find((t) => t.entity_id === id)?.name || id);
  return (
    <li className="space-y-2 px-4 py-3" data-testid="person">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">{person.name}</p>
          <p className="text-xs text-slate-400">
            {person.jarvis_user ? `Jarvis user ${person.jarvis_user}` : 'No Jarvis user'}
            {person.state ? ` · ${person.state.replace('_', ' ')}` : ''}
          </p>
          <p className="break-words text-xs text-slate-500">{names.length ? names.join(', ') : 'No trackers'}</p>
        </div>
        {!editing && (
          <span className="flex gap-2">
            <button type="button" className={BTN} onClick={() => setEditing(true)} aria-label={`Edit ${person.name}`}>
              <Pencil size={13} /> Edit
            </button>
            <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${person.name}`}
              onClick={async () => {
                if (await confirmDialog({ title: `Remove ${person.name}?`,
                  body: 'Their trackers stay. Automations that use this person stop finding them.', confirmLabel: 'Remove', danger: true })) {
                  remove.mutate();
                }
              }}>
              <Trash2 size={13} />
            </button>
          </span>
        )}
      </div>
      {editing && (
        <PersonEditor initial={{ name: person.name, jarvis_user: person.jarvis_user, device_trackers: person.device_trackers }}
          trackers={trackers} taken={taken} users={users} submitLabel="Save" busy={save.isPending}
          onSubmit={(b) => save.mutate(b)} onCancel={() => setEditing(false)} />
      )}
    </li>
  );
}

/** People: who the hub knows, the Jarvis user each one is, and the trackers that say where they are. */
export default function PersonsPanel() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: PERSONS, queryFn: getPersons, retry: false });
  const family = useQuery({ queryKey: ['family-roster'], queryFn: () => api.getFamily(), retry: false, staleTime: 10 * 60 * 1000 });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: PERSONS });
  const [adding, setAdding] = useState(false);
  const add = useMutation({
    mutationFn: (body: PersonBody) => createPerson(body),
    onSuccess: (p) => { toast.success(`${p.name} was added.`); setAdding(false); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const persons = list.data?.persons ?? [];
  const trackers = list.data?.trackers ?? [];
  const taken = new Set(persons.flatMap((p) => p.device_trackers));
  const users = (family.data ?? []).map((m) => m.username);
  return (
    <SetupSection title="People" icon={Users} testId="persons">
      {list.isLoading && <p className="p-4 text-sm text-slate-500">Loading people…</p>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(list.error)}</p>}
      {list.data && persons.length === 0 && <p className="p-4 text-sm text-slate-500">No people yet.</p>}
      <ul className="divide-y divide-white/5">
        {persons.map((p) => <PersonRow key={p.id} person={p} trackers={trackers} taken={taken} users={users} onChanged={refresh} />)}
      </ul>
      <div className="border-t border-white/5 px-4 py-3">
        {adding ? (
          <PersonEditor initial={{ name: '', jarvis_user: null, device_trackers: [] }} trackers={trackers} taken={taken}
            users={users} submitLabel="Add person" busy={add.isPending} onSubmit={(b) => add.mutate(b)} onCancel={() => setAdding(false)} />
        ) : (
          <button type="button" className={BTN} onClick={() => setAdding(true)}><Plus size={14} /> New person</button>
        )}
      </div>
    </SetupSection>
  );
}

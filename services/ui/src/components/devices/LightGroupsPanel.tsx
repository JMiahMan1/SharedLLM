import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Check, Lightbulb, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  LIGHT_GROUPS_KEY, choicesByRoom, createLightGroup, deleteLightGroup, draftOf, draftProblem, getLightGroups,
  memberSummary, toggleLight, updateLightGroup, type LightGroup, type LightGroupDraft, type LightGroups,
} from '../../lib/lightGroups';
import { confirmDialog } from '../ui/confirmDialog';
import SetupSection from './SetupSection';
import { BTN, DANGER, INPUT } from './setupStyles';

const BADGE = 'rounded-md px-1.5 py-0.5 text-[11px] font-medium';

function useRefresh() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: LIGHT_GROUPS_KEY });
    void qc.invalidateQueries({ queryKey: ['homehub-hardware'] });
    void qc.invalidateQueries({ queryKey: ['dashboard', 'rooms'] });
  };
}

/** Make or change one group: its name, its room and its lights (by room, searchable). */
function GroupEditor({ data, group, onDone }: { data: LightGroups; group?: LightGroup; onDone: () => void }) {
  const refresh = useRefresh();
  const [draft, setDraft] = useState<LightGroupDraft>(() => draftOf(group));
  const [query, setQuery] = useState('');
  const [tried, setTried] = useState(false);
  const id = useId();
  const save = useMutation({
    mutationFn: () => (group ? updateLightGroup(group.entry_id, draft) : createLightGroup(draft)),
    onSuccess: (g) => {
      toast.success(group ? `${g.name} was saved.` : `${g.name} was made. It switches its lights together.`);
      refresh();
      onDone();
    },
    onError: (err) => toast.error(failureText(err)),
  });
  const problem = draftProblem(draft);
  const rooms = choicesByRoom(data, draft, query, group?.members);
  const title = group ? `Change ${group.name}` : 'New light group';

  return (
    <form className="space-y-3 border-b border-white/5 bg-white/[0.03] p-3 sm:p-4" data-testid="light-group-editor"
      aria-label={title}
      onSubmit={(e) => { e.preventDefault(); setTried(true); if (!problem) save.mutate(); }}>
      <h3 className="text-sm font-semibold text-white">{title}</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor={`${id}-name`} className="text-xs text-slate-400">Name</label>
          <input id={`${id}-name`} className={INPUT} value={draft.name} placeholder="Kitchen lights" autoFocus maxLength={80}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </div>
        <div className="space-y-1">
          <label htmlFor={`${id}-room`} className="text-xs text-slate-400">Room</label>
          <select id={`${id}-room`} className={INPUT} value={draft.area_id}
            onChange={(e) => setDraft({ ...draft, area_id: e.target.value })}>
            <option value="">No room</option>
            {data.areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
          </select>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-300" aria-live="polite" data-testid="light-group-count">
          {draft.entities.length} {draft.entities.length === 1 ? 'light' : 'lights'} chosen
        </p>
        <label className="relative min-w-0 flex-1 basis-48 sm:max-w-xs">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
          <input className={`${INPUT} pl-8`} value={query} placeholder="Find a light or room" aria-label="Find a light or room"
            onChange={(e) => setQuery(e.target.value)} />
        </label>
      </div>
      <div className="max-h-[22rem] space-y-3 overflow-y-auto rounded-xl border border-white/10 bg-black/20 p-2"
        data-testid="light-group-choices">
        {rooms.length === 0 && <p className="p-2 text-sm text-slate-500">No lights match.</p>}
        {rooms.map(({ room, lights }) => {
          const ids = lights.map((l) => l.entity_id);
          const all = ids.every((id) => draft.entities.includes(id));
          return (
            <fieldset key={room} className="space-y-1" data-testid="light-group-room">
              <legend className="flex w-full items-center justify-between gap-2 px-1">
                <span className="text-xs font-semibold text-slate-400">{room}</span>
                {ids.length > 1 && (
                  <button type="button" className={`${BTN} px-2 text-[11px]`}
                    aria-label={`${all ? 'Clear' : 'Choose'} every light in ${room}`}
                    onClick={() => setDraft({ ...draft, entities: all
                      ? draft.entities.filter((e) => !ids.includes(e))
                      : [...draft.entities, ...ids.filter((id) => !draft.entities.includes(id))] })}>
                    {all ? 'Clear all' : 'Choose all'}
                  </button>
                )}
              </legend>
              {lights.map((l) => {
                const on = draft.entities.includes(l.entity_id);
                return (
                  // A whole row is the target (44 px), drawn as a check box: phones get a thumb-sized tap.
                  <button key={l.entity_id} type="button" role="checkbox" aria-checked={on}
                    className="flex min-h-11 w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                    onClick={() => setDraft(toggleLight(draft, l.entity_id))}>
                    <span aria-hidden className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                      on ? 'border-sky-400 bg-sky-500 text-white' : 'border-white/30 bg-black/30'}`}>
                      {on && <Check size={14} strokeWidth={3} />}
                    </span>
                    <span className="min-w-0 flex-1 break-words text-sm text-slate-100">{l.name}</span>
                    {room === 'No longer in the hub' && <span className={`${BADGE} bg-rose-500/15 text-rose-200`}>Gone</span>}
                    {l.hidden && <span className={`${BADGE} bg-white/10 text-slate-300`}>Hidden copy</span>}
                    {!l.available && room !== 'No longer in the hub' && (
                      <span className={`${BADGE} bg-amber-500/15 text-amber-200`}>Not responding</span>
                    )}
                  </button>
                );
              })}
            </fieldset>
          );
        })}
      </div>
      {tried && problem && <p className="text-xs text-amber-300/90" role="alert">{problem}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={BTN} onClick={onDone}>Cancel</button>
        <button type="submit" className={`${BTN} border-sky-400/40 bg-sky-500/15 text-sky-100`} disabled={save.isPending}>
          {group ? 'Save' : 'Make group'}
        </button>
      </div>
    </form>
  );
}

function GroupRow({ group, onEdit }: { group: LightGroup; onEdit: () => void }) {
  const refresh = useRefresh();
  const remove = useMutation({
    mutationFn: () => deleteLightGroup(group.entry_id),
    onSuccess: () => { toast.success(`${group.name} was removed. Its lights stay as they are.`); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  return (
    <li className="space-y-2 px-3 py-3 sm:px-4" data-testid="light-group" aria-label={group.name}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 basis-48">
          <h3 className="break-words text-sm font-semibold text-white">{group.name}</h3>
          <p className="mt-0.5 text-xs text-slate-400">
            {[group.area ?? 'No room', memberSummary(group)].join(' · ')}
          </p>
        </div>
        <span className="flex gap-2">
          <button type="button" className={BTN} onClick={onEdit} aria-label={`Change ${group.name}`}>
            <Pencil size={13} aria-hidden /> Change
          </button>
          <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${group.name}`}
            onClick={async () => {
              if (await confirmDialog({ title: `Remove ${group.name}?`,
                body: 'The lights stay as they are. Schedules and automations that use the group stop finding it.',
                confirmLabel: 'Remove', danger: true })) remove.mutate();
            }}>
            <Trash2 size={13} aria-hidden />
          </button>
        </span>
      </div>
      <ul className="flex flex-wrap gap-1.5" aria-label={`Lights in ${group.name}`}>
        {group.members.map((m) => (
          <li key={m.entity_id} data-testid="light-group-member"
            className={`inline-flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-1 text-xs ${
              m.missing ? 'border-rose-500/30 text-rose-200' : 'border-white/10 text-slate-200'}`}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${m.state === 'on' ? 'bg-amber-300' : 'bg-slate-600'}`} aria-hidden />
            <span className="min-w-0 break-words">{m.name}</span>
            {m.missing && <span className="text-rose-300/90">gone</span>}
            {m.hidden && <span className="text-slate-500">hidden copy</span>}
          </li>
        ))}
      </ul>
    </li>
  );
}

/**
 * Light groups (parents and admins): several lights that switch as one. Each group is a light
 * in the hub (its room, schedules, automations, voice), made from lights picked by room.
 */
export default function LightGroupsPanel() {
  const list = useQuery({ queryKey: LIGHT_GROUPS_KEY, queryFn: getLightGroups, retry: false });
  const [editing, setEditing] = useState<string | null>(null); // 'new' or a group's entry id
  const data = list.data;
  const editingGroup = data?.groups.find((g) => g.entry_id === editing);

  return (
    <SetupSection title="Light groups" icon={Lightbulb} testId="light-groups"
      aside={data && editing === null ? (
        <button type="button" className={BTN} onClick={() => setEditing('new')}>
          <Plus size={14} aria-hidden /> New group
        </button>
      ) : undefined}>
      <p className="px-3 pt-3 text-sm text-slate-400 sm:px-4">
        Group lights that belong together. A group is one light in its room, so schedules, automations and
        voice can use it: &ldquo;turn off the kitchen lights&rdquo;.
      </p>
      {list.isLoading && <p className="p-4 text-sm text-slate-500" role="status">Loading light groups…</p>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(list.error)}</p>}
      {data && editing !== null && (editing === 'new' || editingGroup) && (
        <div className="mt-3">
          <GroupEditor key={editing} data={data} group={editingGroup} onDone={() => setEditing(null)} />
        </div>
      )}
      {data && data.groups.length === 0 && editing === null && (
        <p className="px-3 py-4 text-sm text-slate-500 sm:px-4" data-testid="light-groups-empty">
          No light groups yet. Choose New group to put lights together.
        </p>
      )}
      {data && data.groups.length > 0 && (
        <ul className="mt-2 divide-y divide-white/5">
          {data.groups.map((g) => (
            <GroupRow key={g.entry_id} group={g} onEdit={() => setEditing(g.entry_id)} />
          ))}
        </ul>
      )}
    </SetupSection>
  );
}

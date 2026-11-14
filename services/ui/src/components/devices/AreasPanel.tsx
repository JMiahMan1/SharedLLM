import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Building2, DoorOpen, Pencil, Plus, Trash2 } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  areasByFloor, createArea, createFloor, deleteArea, deleteFloor, getAreas, getFloors, updateArea, updateFloor,
  type Area, type Floor,
} from '../../lib/homeSetup';
import { confirmDialog } from '../ui/confirmDialog';
import SetupSection from './SetupSection';
import { BTN, DANGER, INPUT } from './setupStyles';

const AREAS = ['homehub-areas'];
const FLOORS = ['homehub-floors'];

function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: AREAS });
    void queryClient.invalidateQueries({ queryKey: FLOORS });
    void queryClient.invalidateQueries({ queryKey: ['homehub-hardware'] });
  };
}

function levelOf(text: string): number | null {
  const n = Number.parseInt(text, 10);
  return Number.isFinite(n) ? n : null;
}

function FloorRow({ floor }: { floor: Floor }) {
  const refresh = useRefresh();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(floor.name);
  const [level, setLevel] = useState(floor.level == null ? '' : String(floor.level));
  const save = useMutation({
    mutationFn: () => updateFloor(floor.floor_id, { name: name.trim(), level: levelOf(level) }),
    onSuccess: () => { setEditing(false); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: () => deleteFloor(floor.floor_id),
    onSuccess: () => { toast.success(`${floor.name} was removed.`); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  if (editing) {
    return (
      <li className="px-4 py-3">
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) save.mutate(); }}>
          <input className={`${INPUT} flex-1 basis-40`} value={name} autoFocus aria-label={`New name for ${floor.name}`}
            onChange={(e) => setName(e.target.value)} />
          <input className={`${INPUT} basis-24 sm:w-24`} value={level} inputMode="numeric" placeholder="Level"
            aria-label={`Level of ${floor.name}`} onChange={(e) => setLevel(e.target.value)} />
          <button type="submit" className={BTN} disabled={save.isPending || !name.trim()}>Save</button>
          <button type="button" className={BTN} onClick={() => setEditing(false)}>Cancel</button>
        </form>
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-4 py-2" data-testid="floor">
      <span className="min-w-0 truncate text-sm text-white">
        {floor.name}{floor.level != null && <span className="ml-2 text-xs text-slate-500">level {floor.level}</span>}
      </span>
      <span className="flex gap-2">
        <button type="button" className={BTN} onClick={() => setEditing(true)} aria-label={`Rename ${floor.name}`}>
          <Pencil size={13} /> Rename
        </button>
        <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${floor.name}`}
          onClick={async () => {
            if (await confirmDialog({ title: `Remove ${floor.name}?`, body: 'Its rooms stay, on no floor.',
              confirmLabel: 'Remove', danger: true })) remove.mutate();
          }}>
          <Trash2 size={13} />
        </button>
      </span>
    </li>
  );
}

function AreaRow({ area, floors }: { area: Area; floors: Floor[] }) {
  const refresh = useRefresh();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(area.name);
  const save = useMutation({
    mutationFn: (body: Parameters<typeof updateArea>[1]) => updateArea(area.area_id, body),
    onSuccess: () => { setEditing(false); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: () => deleteArea(area.area_id),
    onSuccess: () => { toast.success(`${area.name} was removed.`); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  return (
    <li className="space-y-2 px-4 py-3" data-testid="area">
      {editing ? (
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) save.mutate({ name: name.trim() }); }}>
          <input className={`${INPUT} flex-1 basis-40`} value={name} autoFocus aria-label={`New name for ${area.name}`}
            onChange={(e) => setName(e.target.value)} />
          <button type="submit" className={BTN} disabled={save.isPending || !name.trim()}>Save</button>
          <button type="button" className={BTN} onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <p className="truncate text-sm font-semibold text-white">{area.name}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <select className={`${INPUT} flex-1 basis-40 sm:max-w-xs`} value={area.floor_id || ''} aria-label={`Floor of ${area.name}`}
          disabled={save.isPending} onChange={(e) => save.mutate({ floor_id: e.target.value || null })}>
          <option value="">No floor</option>
          {floors.map((f) => <option key={f.floor_id} value={f.floor_id}>{f.name}</option>)}
        </select>
        {!editing && (
          <button type="button" className={BTN} onClick={() => setEditing(true)} aria-label={`Rename ${area.name}`}>
            <Pencil size={13} /> Rename
          </button>
        )}
        <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${area.name}`}
          onClick={async () => {
            if (await confirmDialog({ title: `Remove ${area.name}?`,
              body: 'Its devices keep working, with no room. Automations that name the room stop finding it.',
              confirmLabel: 'Remove', danger: true })) remove.mutate();
          }}>
          <Trash2 size={13} />
        </button>
      </div>
    </li>
  );
}

/** Rooms and floors: add, rename, remove, and put each room on a floor. */
export default function AreasPanel() {
  const refresh = useRefresh();
  const areas = useQuery({ queryKey: AREAS, queryFn: getAreas, retry: false });
  const floors = useQuery({ queryKey: FLOORS, queryFn: getFloors, retry: false });
  const [newArea, setNewArea] = useState('');
  const [newAreaFloor, setNewAreaFloor] = useState('');
  const [newFloor, setNewFloor] = useState('');
  const [newLevel, setNewLevel] = useState('');
  const addArea = useMutation({
    mutationFn: () => createArea({ name: newArea.trim(), floor_id: newAreaFloor || null }),
    onSuccess: (a) => { toast.success(`${a.name} was added.`); setNewArea(''); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const addFloor = useMutation({
    mutationFn: () => createFloor({ name: newFloor.trim(), level: levelOf(newLevel) }),
    onSuccess: (f) => { toast.success(`${f.name} was added.`); setNewFloor(''); setNewLevel(''); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const floorList = floors.data ?? [];
  const groups = areasByFloor(areas.data ?? [], floorList);
  const error = areas.error || floors.error;

  return (
    <SetupSection title="Rooms & floors" icon={DoorOpen} testId="areas">
      {(areas.isLoading || floors.isLoading) && <p className="p-4 text-sm text-slate-500">Loading rooms…</p>}
      {error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(error)}</p>}

      <div className="border-b border-white/5" data-testid="floors">
        <p className="flex items-center gap-2 px-4 pt-3 text-xs font-semibold text-slate-300"><Building2 size={13} /> Floors</p>
        {floors.data && floorList.length === 0 && <p className="px-4 py-2 text-xs text-slate-500">No floors yet.</p>}
        <ul className="divide-y divide-white/5">{floorList.map((f) => <FloorRow key={f.floor_id} floor={f} />)}</ul>
        <form className="flex flex-wrap items-end gap-2 px-4 py-3"
          onSubmit={(e) => { e.preventDefault(); if (newFloor.trim()) addFloor.mutate(); }}>
          <input className={`${INPUT} flex-1 basis-40`} value={newFloor} placeholder="New floor" aria-label="New floor name"
            onChange={(e) => setNewFloor(e.target.value)} />
          <input className={`${INPUT} basis-24 sm:w-24`} value={newLevel} inputMode="numeric" placeholder="Level"
            aria-label="New floor level" onChange={(e) => setNewLevel(e.target.value)} />
          <button type="submit" className={BTN} disabled={addFloor.isPending || !newFloor.trim()}><Plus size={14} /> Add floor</button>
        </form>
      </div>

      {groups.map((g) => (
        <div key={g.floor?.floor_id ?? 'none'} data-testid="area-group">
          <p className="px-4 pt-3 text-xs font-semibold text-slate-400">{g.floor ? g.floor.name : 'On no floor'}</p>
          {g.areas.length === 0 && <p className="px-4 py-2 text-xs text-slate-500">No rooms on this floor.</p>}
          <ul className="divide-y divide-white/5">{g.areas.map((a) => <AreaRow key={a.area_id} area={a} floors={floorList} />)}</ul>
        </div>
      ))}

      <form className="flex flex-wrap items-end gap-2 border-t border-white/5 px-4 py-3"
        onSubmit={(e) => { e.preventDefault(); if (newArea.trim()) addArea.mutate(); }}>
        <input className={`${INPUT} flex-1 basis-40`} value={newArea} placeholder="New room" aria-label="New room name"
          onChange={(e) => setNewArea(e.target.value)} />
        <select className={`${INPUT} flex-1 basis-32 sm:max-w-xs`} value={newAreaFloor} aria-label="Floor of the new room"
          onChange={(e) => setNewAreaFloor(e.target.value)}>
          <option value="">No floor</option>
          {floorList.map((f) => <option key={f.floor_id} value={f.floor_id}>{f.name}</option>)}
        </select>
        <button type="submit" className={BTN} disabled={addArea.isPending || !newArea.trim()}><Plus size={14} /> Add room</button>
      </form>
    </SetupSection>
  );
}

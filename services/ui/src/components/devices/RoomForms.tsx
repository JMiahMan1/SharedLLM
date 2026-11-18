import { useState } from 'react';
import { Plus } from 'lucide-react';
import { useRoomActions } from '../../hooks/useRoomActions';
import type { Area } from '../../lib/homeSetup';
import { BTN, INPUT } from './setupStyles';

interface FloorChoice { floor_id: string; name?: string }

/** A new room: its name and, when the home has floors, which floor it is on. */
export function AddRoomForm({ floors, onAdded, autoFocus = false, className = '' }: {
  floors: FloorChoice[];
  onAdded?: (area: Area) => void;
  autoFocus?: boolean;
  className?: string;
}) {
  const { add } = useRoomActions();
  const [name, setName] = useState('');
  const [floor, setFloor] = useState('');
  return (
    <form className={`flex flex-wrap items-end gap-2 ${className}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        add.mutate({ name, floor_id: floor || null }, { onSuccess: (area) => { setName(''); onAdded?.(area); } });
      }}>
      <input className={`${INPUT} flex-1 basis-40`} value={name} placeholder="New room" aria-label="New room name"
        autoFocus={autoFocus} onChange={(e) => setName(e.target.value)} />
      <select className={`${INPUT} flex-1 basis-32 sm:max-w-xs`} value={floor} aria-label="Floor of the new room"
        onChange={(e) => setFloor(e.target.value)}>
        <option value="">No floor</option>
        {floors.map((f) => <option key={f.floor_id} value={f.floor_id}>{f.name || 'Floor'}</option>)}
      </select>
      <button type="submit" className={BTN} disabled={add.isPending || !name.trim()}><Plus size={14} aria-hidden /> Add room</button>
    </form>
  );
}

/** A room's new name, saved on Save; Cancel leaves it as it was. */
export function RenameRoomForm({ room, onDone, className = '' }: {
  room: Pick<Area, 'area_id' | 'name'>;
  onDone: () => void;
  className?: string;
}) {
  const { update } = useRoomActions();
  const [name, setName] = useState(room.name);
  return (
    <form className={`flex flex-wrap gap-2 ${className}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) update.mutate({ id: room.area_id, body: { name: name.trim() } }, { onSuccess: onDone });
      }}>
      <input className={`${INPUT} flex-1 basis-40`} value={name} autoFocus aria-label={`New name for ${room.name}`}
        onChange={(e) => setName(e.target.value)} />
      <button type="submit" className={BTN} disabled={update.isPending || !name.trim()}>Save</button>
      <button type="button" className={BTN} onClick={onDone}>Cancel</button>
    </form>
  );
}

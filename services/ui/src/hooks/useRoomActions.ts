import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { confirmDialog } from '../components/ui/confirmDialog';
import { failureText } from '../lib/discoveredDevices';
import { createArea, deleteArea, updateArea, type Area } from '../lib/homeSetup';

/** The hub's rooms and floors, as Devices > Rooms & places reads them. */
export const AREAS_KEY = ['homehub-areas'];
export const FLOORS_KEY = ['homehub-floors'];
/** The Rooms page's rooms with their devices. */
export const ROOMS_KEY = ['dashboard', 'rooms'];

/** What a person is told before a room is removed. */
export const REMOVE_ROOM_BODY = 'Its devices keep working, with no room. Automations that name the room stop finding it.';

/** Every list that shows rooms reads again: Rooms & places, the device list and the Rooms page. */
export function useRoomsRefresh(): () => void {
  const queryClient = useQueryClient();
  return () => {
    for (const queryKey of [AREAS_KEY, FLOORS_KEY, ['homehub-hardware'], ROOMS_KEY]) {
      void queryClient.invalidateQueries({ queryKey });
    }
  };
}

type RoomRef = Pick<Area, 'area_id' | 'name'>;

/**
 * Adding, renaming, moving and removing a room, the same way on the Rooms page and in
 * Devices > Rooms & places. Each mutation says what happened and refreshes every list of rooms.
 * The hub only lets an admin do any of it; callers show these controls to admins only.
 */
export function useRoomActions() {
  const refresh = useRoomsRefresh();
  const onError = (err: unknown) => { toast.error(failureText(err)); };

  const add = useMutation({
    mutationFn: ({ name, floor_id }: { name: string; floor_id?: string | null }) =>
      createArea({ name: name.trim(), floor_id: floor_id || null }),
    onSuccess: (area) => { toast.success(`${area.name} was added.`); refresh(); },
    onError,
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof updateArea>[1] }) => updateArea(id, body),
    onSuccess: () => refresh(),
    onError,
  });

  const remove = useMutation({
    mutationFn: (room: RoomRef) => deleteArea(room.area_id),
    onSuccess: (_result, room) => { toast.success(`${room.name} was removed.`); refresh(); },
    onError,
  });

  /** Asks first, then removes. Resolves to whether the person said yes. */
  const confirmRemove = async (room: RoomRef): Promise<boolean> => {
    const yes = await confirmDialog({ title: `Remove ${room.name}?`, body: REMOVE_ROOM_BODY, confirmLabel: 'Remove', danger: true });
    if (yes) remove.mutate(room);
    return yes;
  };

  return { add, update, remove, confirmRemove };
}

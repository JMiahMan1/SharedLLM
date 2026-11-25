import { useCallback, useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import FamilyMesh, { type MeshDiagnostics, type MeshMessage, type MeshRoom } from '../../plugins/familyMesh';

/**
 * The offline chat in Chat (Android app, on when Settings > Nearby & offline
 * says so): every chat room the person is in works with no Wi-Fi or data.
 * Messages are sealed with that room's own key on the phone, carried by every
 * family phone nearby over Bluetooth, opened only by the room's members, and
 * posted into the room by the author's phone once it is online again
 * (FamilyMesh / MeshRules on the phone, services/gateway/family_mesh.py).
 */
export interface OfflineChat {
  /** The offline chat is on on this phone. */
  enabled: boolean;
  diag: MeshDiagnostics | null;
  /** The rooms this phone holds a key for. */
  rooms: MeshRoom[];
  /** Every message this phone holds and may read, oldest first. */
  messages: MeshMessage[];
  reload: () => void;
}

export function useOfflineChat(): OfflineChat {
  const android = Capacitor.getPlatform() === 'android';
  const [diag, setDiag] = useState<MeshDiagnostics | null>(null);
  const [rooms, setRooms] = useState<MeshRoom[]>([]);
  const [messages, setMessages] = useState<MeshMessage[]>([]);

  const reload = useCallback(() => {
    Promise.all([FamilyMesh.getDiagnostics(), FamilyMesh.rooms(), FamilyMesh.list()])
      .then(([d, r, l]) => {
        setDiag(d);
        setRooms(r.rooms ?? []);
        setMessages(l.messages ?? []);
      })
      .catch(() => setDiag(null));
  }, []);

  useEffect(() => {
    if (!android) return undefined;
    reload();
    let remove: (() => Promise<void>) | undefined;
    let gone = false;
    void FamilyMesh.addListener('change', reload).then((h) => {
      if (gone) void h.remove();
      else remove = h.remove;
    });
    return () => {
      gone = true;
      void remove?.();
    };
  }, [android, reload]);

  return { enabled: android && Boolean(diag?.enabled), diag, rooms, messages, reload };
}


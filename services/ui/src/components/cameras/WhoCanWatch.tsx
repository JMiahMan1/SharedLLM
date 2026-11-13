import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, Undo2 } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { getCameraViewers, permitListAfter, viewersSummary, type CameraViewer } from '../../lib/cameraViewers';
import { Toggle } from '../ui/Toggle';

const NOTE = 'Set on the Cameras page (Who can watch)';

/**
 * Camera viewer > Who can watch (admins): one switch per family member. A
 * change locks the camera to the people switched on, through the same device
 * lock every device has, so it holds everywhere Jarvis shows cameras. Admins
 * (and roles that may use locked devices) can always watch.
 */
export default function WhoCanWatch({ entityId, cameraName }: { entityId: string; cameraName: string }) {
  const queryClient = useQueryClient();
  const key = ['camera-viewers', entityId];
  const viewers = useQuery({ queryKey: key, queryFn: () => getCameraViewers(entityId), retry: false });

  const done = () => {
    void queryClient.invalidateQueries({ queryKey: key });
    void queryClient.invalidateQueries({ queryKey: ['entity-protection'] });
  };
  const change = useMutation({
    mutationFn: ({ person, watch }: { person: CameraViewer; watch: boolean }) => api.setEntityProtection(entityId, {
      protected: true, permitted_usernames: permitListAfter(viewers.data?.people ?? [], person.username, watch), note: NOTE,
    }),
    onSuccess: (_out, { person, watch }) => {
      toast.success(`${person.name} ${watch ? 'can now watch' : 'can no longer watch'} ${cameraName}`);
      done();
    },
    onError: (err) => toast.error(errorText(err, 'Couldn’t change who can watch')),
  });
  const reset = useMutation({
    mutationFn: () => api.setEntityProtection(entityId, { protected: false, permitted_usernames: [] }),
    onSuccess: () => { toast.success(`${cameraName} follows each person’s device access again`); done(); },
    onError: (err) => toast.error(errorText(err, 'Couldn’t change who can watch')),
  });

  if (viewers.isLoading) {
    return <div className="flex justify-center py-4" role="status" aria-label="Loading who can watch"><Loader2 className="animate-spin text-slate-500" /></div>;
  }
  if (viewers.isError || !viewers.data) {
    return <p className="text-sm text-red-300">{errorText(viewers.error, 'Couldn’t read who can watch')}</p>;
  }
  const v = viewers.data;
  const busy = change.isPending || reset.isPending;

  return (
    <div className="space-y-3" data-testid="who-can-watch">
      <p className="text-xs text-slate-400">{viewersSummary(v)}</p>
      <ul className="divide-y divide-white/5 rounded-xl border border-white/10 bg-white/5" aria-label="Family members">
        {v.people.map((p) => (
          <li key={p.username} className="flex min-h-12 items-center gap-3 px-3 py-1.5">
            <span className="min-w-0 flex-1">
              <span className="block break-words text-sm text-white">{p.name}</span>
              <span className="block text-[11px] text-slate-400">
                {p.is_admin ? 'Admin: can always watch' : p.always ? 'Can always watch (their role)'
                  : p.can_watch ? 'Can watch' : 'Can’t watch'}
              </span>
            </span>
            <Toggle checked={p.can_watch} disabled={p.always || busy} ariaLabel={`${p.name} can watch ${cameraName}`}
              onChange={(watch) => change.mutate({ person: p, watch })} />
          </li>
        ))}
      </ul>
      {v.limited && (
        <button type="button" onClick={() => reset.mutate()} disabled={busy}
          className="min-h-11 flex items-center gap-2 rounded-xl bg-white/5 border border-white/10 px-4 text-sm text-white hover:bg-white/10 disabled:opacity-50">
          {reset.isPending ? <Loader2 size={16} className="animate-spin" /> : <Undo2 size={16} />}
          Use each person’s device access again
        </button>
      )}
    </div>
  );
}

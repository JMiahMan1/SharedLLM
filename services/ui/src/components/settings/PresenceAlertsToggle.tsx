import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';

/**
 * Family arrival / departure notices: "Jeremiah left Work · home in about
 * 22 min", "Michele arrived at Home". Sent for the people who share their
 * location with you; on unless switched off here.
 */
export default function PresenceAlertsToggle() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ['presence-alerts'], queryFn: () => api.getPresenceAlerts() });
  const save = useMutation({
    mutationFn: (enabled: boolean) => api.setPresenceAlerts(enabled),
    onSuccess: (r) => {
      queryClient.setQueryData(['presence-alerts'], r);
      toast.success(r.enabled ? 'Arrival and departure alerts on' : 'Arrival and departure alerts off');
    },
    onError: () => toast.error('Could not save the setting'),
  });
  const enabled = data?.enabled ?? true;
  return (
    <div className="glass-panel rounded-2xl p-4 flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-white">Arrival and departure alerts</p>
        <p className="text-xs text-slate-400 mt-0.5">
          When family who share their location with you arrive at or leave a saved place, with the drive time home.
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label="Arrival and departure alerts"
        disabled={!data || save.isPending}
        onClick={() => save.mutate(!enabled)}
        className={`relative shrink-0 h-7 w-12 rounded-full transition min-h-11 pointer-coarse:min-h-11 flex items-center ${enabled ? 'bg-purple-500/70' : 'bg-white/10'}`}
      >
        <span className={`h-5 w-5 rounded-full bg-white transition-transform ${enabled ? 'translate-x-6' : 'translate-x-1'}`} />
      </button>
    </div>
  );
}

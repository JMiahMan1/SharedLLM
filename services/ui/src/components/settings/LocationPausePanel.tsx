import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { EyeOff, Play } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';

const CHOICES: { label: string; minutes: number | null }[] = [
  { label: '1 hour', minutes: 60 },
  { label: '8 hours', minutes: 8 * 60 },
  { label: 'Until I turn it back on', minutes: null },
];

const KEY = ['location-pause'] as const;

/**
 * Pause location sharing (adults only; children and teens are not offered it
 * and the server refuses them). While paused the family sees "Location
 * paused" instead of where, and no arrival alerts go out about it.
 */
export default function LocationPausePanel() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: KEY, queryFn: () => api.getLocationPause(), retry: false, staleTime: 60_000 });
  const pause = useMutation({
    mutationFn: (minutes: number | null) => api.setLocationPause(minutes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KEY });
      toast.success('Location sharing paused');
    },
    onError: (err) => toast.error(errorText(err, 'Could not pause location sharing')),
  });
  const resume = useMutation({
    mutationFn: () => api.clearLocationPause(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KEY });
      toast.success('Location sharing is back on');
    },
    onError: (err) => toast.error(errorText(err, 'Could not turn location sharing back on')),
  });
  if (!data?.allowed) return null;
  const until = data.until ? new Date(data.until * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : null;
  return (
    <section className="glass-panel rounded-2xl p-4 space-y-3" aria-labelledby="location-pause-heading" data-testid="location-pause-panel">
      <div className="px-1">
        <h2 id="location-pause-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <EyeOff size={16} className="text-purple-400" />
          Pause location sharing
        </h2>
        <p className="text-xs text-slate-500 mt-1" data-testid="location-pause-state">
          {data.paused
            ? `Paused${until ? ` until ${until}` : ' until you turn it back on'}. Your family sees "Location paused".`
            : 'Your family sees where you are. Pausing hides it for a while; they see that it is paused.'}
        </p>
      </div>
      {data.paused ? (
        <button
          type="button"
          onClick={() => resume.mutate()}
          disabled={resume.isPending}
          className="glass-button flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-semibold text-emerald-200 disabled:opacity-50"
        >
          <Play size={14} /> Share my location again
        </button>
      ) : (
        <div className="flex flex-wrap gap-2">
          {CHOICES.map((c) => (
            <button
              key={c.label}
              type="button"
              onClick={() => pause.mutate(c.minutes)}
              disabled={pause.isPending}
              className="min-h-11 rounded-xl bg-white/5 px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50"
            >
              {c.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

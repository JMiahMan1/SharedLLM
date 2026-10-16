import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { useAuth } from '../../context/AuthContext';
import { api, type NotificationPreferences } from '../../services/api';
import { categoriesFor } from '../../lib/notificationCategories';
import Toggle from '../ui/Toggle';

const QUERY_KEY = ['notification-preferences'];

/**
 * One switch per kind of notice (docs/PUSH.md). The choice is the person's,
 * not the device's: Jarvis stops sending a kind that is off, to the app, the
 * browser and every phone alike. A kind with no saved choice is on.
 */
export default function NotificationPreferencesPanel() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { data, isError, isLoading } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => api.getNotificationPreferences(),
  });
  const categories = categoriesFor(Boolean(user?.is_admin));

  const save = useMutation({
    mutationFn: (next: NotificationPreferences) => api.setNotificationPreferences(next),
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey: QUERY_KEY });
      const previous = queryClient.getQueryData<NotificationPreferences>(QUERY_KEY);
      queryClient.setQueryData(QUERY_KEY, next);
      return { previous };
    },
    onError: (_err, _next, context) => {
      queryClient.setQueryData(QUERY_KEY, context?.previous);
      toast.error('Could not save the setting');
    },
    onSuccess: (saved) => {
      if (Object.keys(saved).length > 0) queryClient.setQueryData(QUERY_KEY, saved);
    },
  });

  const isOn = (id: string) => data?.[id] ?? true;

  const change = (id: string, on: boolean) => {
    const next: NotificationPreferences = { ...(data ?? {}) };
    for (const c of categories) next[c.id] = c.id === id ? on : isOn(c.id);
    save.mutate(next);
  };

  return (
    <section className="glass-panel rounded-2xl p-4 space-y-1" aria-labelledby="notification-kinds-heading">
      <h2 id="notification-kinds-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-1">
        Notifications
      </h2>
      <p className="px-3 text-xs text-slate-400 mb-2">
        Choose what Jarvis tells you about. Applies everywhere you are signed in.
      </p>
      {isError && (
        <p className="px-3 text-xs text-amber-300" role="status">Could not load your choices. Try again later.</p>
      )}
      {categories.map((c) => (
        <div key={c.id} className="flex items-center justify-between gap-3 p-3 min-h-[44px] rounded-xl bg-white/5">
          <div className="min-w-0">
            <p className="text-white text-sm font-medium">{c.label}</p>
            <p className="text-xs text-slate-400">{c.description}</p>
            {c.note && <p className="text-xs text-slate-500 mt-0.5">{c.note}</p>}
          </div>
          <Toggle
            checked={isOn(c.id)}
            onChange={(next) => change(c.id, next)}
            disabled={isLoading || isError || save.isPending}
            ariaLabel={c.label}
          />
        </div>
      ))}
    </section>
  );
}

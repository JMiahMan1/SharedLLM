import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type PushAppRegistration } from '../../services/api';
import ChatNotify from '../../plugins/chatNotify';

const QUERY_KEY = ['push-apps'];

/**
 * Apps on this phone that use Jarvis to receive their push messages
 * (UnifiedPush), with a way to remove one. Removing goes through the app so
 * the other app hears it was unregistered; a registration made on another
 * phone is removed at the gateway only. Android app only.
 */
export default function PushAppsPanel() {
  const queryClient = useQueryClient();
  const { data: apps = [], isError, isLoading } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => api.listPushApps(),
  });
  const packages = Array.from(new Set(apps.map((a) => a.app))).sort();
  const { data: labels = {} } = useQuery({
    queryKey: ['push-app-labels', packages],
    queryFn: async () => (await ChatNotify.appLabels({ packages })).labels ?? {},
    enabled: packages.length > 0,
    // An older APK has no appLabels; package names are shown instead.
    retry: false,
  });

  const remove = useMutation({
    mutationFn: async (registration: PushAppRegistration) => {
      // An older APK has no removeApp; the gateway call below still removes it.
      const removed = await ChatNotify.removeApp({ app: registration.app, token: registration.token })
        .then((r) => r.removed)
        .catch(() => 0);
      if (removed === 0 && registration.token) {
        await api.unregisterPushApp({ app: registration.app, token: registration.token });
      }
    },
    onSuccess: (_r, registration) => {
      toast.success(`${labels[registration.app] ?? registration.app} no longer uses Jarvis for push`);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: () => toast.error('Could not remove the app'),
  });

  return (
    <section className="glass-panel rounded-2xl p-4 space-y-1" aria-labelledby="push-apps-heading">
      <h2 id="push-apps-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-1">
        Apps using Jarvis for push
      </h2>
      <p className="px-3 text-xs text-slate-400 mb-2">
        Apps such as Nextcloud Talk, DAVx5 or a Matrix client can receive their notifications through Jarvis instead
        of Google. Pick Jarvis as the push service (UnifiedPush) in that app&apos;s settings.
      </p>
      {isLoading && (
        <p className="px-3 text-xs text-slate-400 flex items-center gap-2" role="status">
          <Loader2 size={14} className="animate-spin" /> Loading
        </p>
      )}
      {isError && (
        <p className="px-3 text-xs text-amber-300" role="status">Could not load the list. Try again later.</p>
      )}
      {!isLoading && !isError && apps.length === 0 && (
        <p className="px-3 py-2 text-sm text-slate-300">No apps use Jarvis for push yet.</p>
      )}
      {apps.map((registration) => {
        const label = labels[registration.app] ?? registration.app;
        const busy = remove.isPending && remove.variables === registration;
        return (
          <div
            key={`${registration.app}:${registration.token ?? registration.topic ?? ''}`}
            className="flex items-center justify-between gap-3 p-3 min-h-[44px] rounded-xl bg-white/5"
          >
            <div className="min-w-0">
              <p className="text-white text-sm font-medium truncate">{label}</p>
              {label !== registration.app && <p className="text-xs text-slate-400 truncate">{registration.app}</p>}
            </div>
            <button
              type="button"
              onClick={() => remove.mutate(registration)}
              disabled={remove.isPending}
              aria-label={`Remove ${label}`}
              className="shrink-0 inline-flex items-center justify-center gap-1.5 min-h-[44px] min-w-[44px] px-3 rounded-xl text-sm text-red-300 hover:bg-red-500/10 disabled:opacity-40"
            >
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
              <span className="hidden sm:inline">Remove</span>
            </button>
          </div>
        );
      })}
    </section>
  );
}

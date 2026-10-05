import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';

interface SharingState {
  enabled: boolean;
  audience: 'circle' | 'users';
  user_ids: string[];
  share: string[];
}

const SCOPES: Array<{ id: string; label: string; description: string }> = [
  { id: 'totals', label: 'Totals', description: 'Steps, workout distance, and driving' },
  { id: 'workouts', label: 'Workouts', description: 'Workout count and recent workouts' },
  { id: 'achievements', label: 'Achievements', description: 'Points and badges earned' },
  { id: 'bible', label: 'Bible', description: 'Reading streaks, days read and Bible badges — never what was read or highlighted' },
];

/**
 * Opt-in activity sharing. Private by default — nothing is visible to anyone
 * else until this is switched on and saved.
 */
export default function ActivitySharingPanel() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<SharingState | null>(null);
  const [saving, setSaving] = useState(false);

  const { data: server } = useQuery({
    queryKey: ['activity-sharing'],
    queryFn: () => api.getActivitySharing(),
  });

  // `getSharingRecipients`, not `getUsers`: the latter is admin-only, which
  // rendered a non-admin's picker empty and left "Everyone" as the only
  // audience they could pick -- the opposite of opting in.
  const { data: users = [] } = useQuery({
    queryKey: ['sharing-recipients'],
    queryFn: () => api.getSharingRecipients(),
  });

  const state: SharingState = draft ??
    (server
      ? {
          enabled: server.enabled,
          audience: server.audience === 'users' ? 'users' : 'circle',
          user_ids: server.user_ids,
          share: server.share.length ? server.share : ['totals'],
        }
      : { enabled: false, audience: 'circle', user_ids: [], share: ['totals'] });

  // The system `default` account is excluded: geo treats it as a privileged
  // viewer that can read everyone's activity regardless of consent, so
  // offering it as a grant target would imply a control it does not have.
  const recipients = useMemo(
    () => users.filter((u) => u.username && u.username !== 'default'),
    [users],
  );

  const patch = (updates: Partial<SharingState>) => {
    setDraft({ ...state, ...updates });
  };

  const toggleScope = (id: string) => {
    const next = state.share.includes(id)
      ? state.share.filter((s) => s !== id)
      : [...state.share, id];
    patch({ share: next });
  };

  const toggleUser = (username: string) => {
    const next = state.user_ids.includes(username)
      ? state.user_ids.filter((u) => u !== username)
      : [...state.user_ids, username];
    patch({ user_ids: next });
  };

  const save = async () => {
    if (state.share.length === 0) {
      toast.error('Pick at least one thing to share');
      return;
    }
    setSaving(true);
    try {
      await api.updateActivitySharing({
        enabled: state.enabled,
        audience: state.audience,
        user_ids: state.audience === 'users' ? state.user_ids : [],
        share: state.share,
      });
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: ['activity-sharing'] });
      toast.success(state.enabled ? 'Activity sharing updated' : 'Activity sharing turned off');
    } catch {
      toast.error('Could not save sharing settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-3" data-testid="activity-sharing-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            Share my activity
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            {state.enabled
              ? 'Your activity is visible to the people you chose below.'
              : 'Private — nothing is visible to anyone else.'}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={state.enabled}
          aria-label="Share my activity"
          data-testid="sharing-toggle"
          onClick={() => patch({ enabled: !state.enabled })}
          className={`relative w-12 h-7 rounded-full transition-colors shrink-0 ${
            state.enabled ? 'bg-emerald-500/70' : 'bg-slate-700'
          }`}
        >
          <span
            className={`absolute top-1 w-5 h-5 rounded-full bg-white transition-all ${
              state.enabled ? 'left-6' : 'left-1'
            }`}
          />
        </button>
      </div>

      {state.enabled && (
        <>
          <div className="flex gap-2">
            {(
              [
                { id: 'circle', label: 'Everyone' },
                { id: 'users', label: 'Specific people' },
              ] as const
            ).map((opt) => (
              <button
                key={opt.id}
                type="button"
                data-testid={`audience-${opt.id}`}
                onClick={() => patch({ audience: opt.id })}
                className={`px-3 py-2 text-xs rounded-lg border min-h-11 transition-colors ${
                  state.audience === opt.id
                    ? 'bg-purple-500/20 border-purple-500/40 text-purple-200'
                    : 'border-white/10 text-slate-400 hover:text-slate-200'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {state.audience === 'users' && (
            <div className="space-y-1.5">
              <p className="text-xs text-slate-500">Who can see it:</p>
              <div className="flex flex-wrap gap-2">
                {recipients.map((u) => (
                    <button
                      key={u.username}
                      type="button"
                      data-testid={`sharing-user-${u.username}`}
                      onClick={() => toggleUser(u.username)}
                      className={`px-3 py-2 text-xs rounded-lg border min-h-11 pointer-coarse:min-h-11 transition-colors ${
                        state.user_ids.includes(u.username)
                          ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-200'
                          : 'border-white/10 text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {u.display_name || u.username}
                    </button>
                  ))}
                {recipients.length === 0 && (
                  <p className="text-xs text-amber-300/80">
                    No other accounts yet. Add someone in Admin → Users before
                    sharing with specific people.
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="space-y-2">
            <p className="text-xs text-slate-500">What to share:</p>
            {SCOPES.map((scope) => (
              <label
                key={scope.id}
                className="flex items-center gap-3 cursor-pointer min-h-11"
              >
                <input
                  type="checkbox"
                  data-testid={`scope-${scope.id}`}
                  checked={state.share.includes(scope.id)}
                  onChange={() => toggleScope(scope.id)}
                  className="w-5 h-5 accent-purple-500"
                />
                <span>
                  <span className="block text-sm text-slate-200">{scope.label}</span>
                  <span className="block text-xs text-slate-500">{scope.description}</span>
                </span>
              </label>
            ))}
          </div>
        </>
      )}

      <button
        type="button"
        onClick={() => void save()}
        disabled={saving}
        data-testid="save-sharing"
        className="glass-button px-4 py-2 text-sm w-full sm:w-auto min-h-11 disabled:opacity-50"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}

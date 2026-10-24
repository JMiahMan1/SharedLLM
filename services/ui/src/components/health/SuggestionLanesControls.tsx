import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import type { RecommendationLane, RecommendationLanes } from '../../types/api';

/**
 * A parent's switches for the kinds of suggestion Jarvis makes to one child
 * (Phase 11 step 3): each lane is blessed (on) or blocked (off). A blocked lane
 * shows nothing in the child's For You widget and reads none of their data.
 * The server refuses anyone else; this only renders once it says the viewer
 * may manage the child.
 */

const LANES: Array<{ lane: RecommendationLane; label: string }> = [
  { lane: 'music', label: 'Music' },
  { lane: 'video', label: 'Videos' },
  { lane: 'podcast', label: 'Podcasts' },
  { lane: 'book', label: 'Books' },
  { lane: 'game', label: 'Games' },
  { lane: 'activity', label: 'Activities' },
];

export default function SuggestionLanesControls({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const key = ['recommendation-lanes', username];
  const { data } = useQuery({
    queryKey: key,
    queryFn: () => api.getRecommendationLanes(username),
    retry: false,
  });
  const toggle = useMutation({
    mutationFn: ({ lane, on }: { lane: RecommendationLane; on: boolean }) => api.setRecommendationLanes(username, { [lane]: on }),
    onSuccess: (saved: RecommendationLanes) => {
      queryClient.setQueryData<RecommendationLanes>(key, (old) => (old ? { ...old, lanes: saved.lanes } : saved));
    },
  });

  if (!data?.can_manage) return null;
  const failureText = isAxiosError(toggle.error)
    ? (toggle.error.response?.data as { detail?: string })?.detail || 'That did not save.'
    : toggle.error
      ? 'That did not save.'
      : null;

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="suggestion-lanes">
      <div className="flex items-center gap-2">
        <Sparkles size={16} className="text-violet-300" />
        <h3 className="text-sm font-semibold text-white">Suggestions for {name}</h3>
      </div>
      <p className="text-xs text-slate-400">Switch off a kind of suggestion and Jarvis stops making it for {name}.</p>
      <ul className="grid grid-cols-2 gap-2">
        {LANES.map(({ lane, label }) => {
          const on = data.lanes[lane];
          return (
            <li key={lane}>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                disabled={toggle.isPending}
                onClick={() => toggle.mutate({ lane, on: !on })}
                className={`w-full min-h-11 rounded-xl border px-3 py-2 text-left text-xs flex items-center justify-between gap-2 ${
                  on ? 'border-violet-400/40 bg-violet-400/10 text-slate-100' : 'border-white/10 bg-white/5 text-slate-400'
                }`}
                data-testid={`suggestion-lane-${lane}`}
              >
                <span>{label}</span>
                <span className="font-semibold">{on ? 'On' : 'Off'}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {failureText && <p className="text-xs text-amber-300">{failureText}</p>}
    </section>
  );
}

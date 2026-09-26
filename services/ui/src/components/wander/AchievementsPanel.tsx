import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Award, Target, Trophy } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ActivityGoals } from '../../types/api';

interface AchievementsPanelProps {
  userId?: string;
}

/**
 * Achievements, next-up progress and goals.
 *
 * Achievements are derived server-side from the same step buckets and workouts
 * the rest of the page shows, so this panel can never disagree with them. The
 * audience/privacy work (sharing with other users) is a later slice — this is
 * the owner's own view.
 */
export default function AchievementsPanel({ userId }: AchievementsPanelProps) {
  const queryClient = useQueryClient();
  const [editingGoals, setEditingGoals] = useState(false);
  const [weeklyDraft, setWeeklyDraft] = useState('');
  const [workoutsDraft, setWorkoutsDraft] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['achievements', userId ?? 'me'],
    queryFn: () => api.getAchievements(userId, 30),
    staleTime: 60_000,
  });

  const goals: ActivityGoals | undefined = data?.goals;
  const earned = data?.earned ?? [];
  const nextUp = data?.next_up ?? [];

  const startEditing = () => {
    setWeeklyDraft(String(goals?.weekly_steps ?? 70000));
    setWorkoutsDraft(String(goals?.workouts_per_week ?? 4));
    setEditingGoals(true);
  };

  const saveGoals = async () => {
    const weekly = Number(weeklyDraft);
    const workouts = Number(workoutsDraft);
    if (!Number.isFinite(weekly) || weekly < 5000 || weekly > 700000) {
      toast.error('Weekly steps must be between 5,000 and 700,000');
      return;
    }
    if (!Number.isFinite(workouts) || workouts < 1 || workouts > 50) {
      toast.error('Workouts per week must be between 1 and 50');
      return;
    }
    try {
      await api.updateGoals({ weekly_steps: weekly, workouts_per_week: workouts }, userId);
      await queryClient.invalidateQueries({ queryKey: ['achievements'] });
      setEditingGoals(false);
      toast.success('Goals updated');
    } catch {
      toast.error('Could not save your goals');
    }
  };

  return (
    <div className="glass-panel p-5 rounded-2xl border border-white/5 space-y-4" data-testid="achievements-panel">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Trophy size={16} className="text-amber-300" />
          <h2 className="text-sm font-semibold text-slate-200">Achievements</h2>
          {data && (
            <span className="text-[11px] text-slate-400" data-testid="achievement-points">
              {earned.length} unlocked · <strong className="text-amber-300">{data.points}</strong> pts
            </span>
          )}
        </div>
        <button
          type="button"
          className="glass-button px-2.5 py-1 text-[11px]"
          onClick={() => (editingGoals ? setEditingGoals(false) : startEditing())}
        >
          <Target size={12} /> Goals
        </button>
      </div>

      {editingGoals && goals && (
        <div className="flex flex-wrap items-end gap-2 text-xs" data-testid="goal-editor">
          <label className="space-y-1 text-slate-400">
            Weekly steps
            <input
              type="number"
              min={5000}
              max={700000}
              step={1000}
              value={weeklyDraft}
              onChange={(e) => setWeeklyDraft(e.target.value)}
              aria-label="Weekly step goal"
              className="glass-input px-2 py-1 text-xs w-28 block"
            />
          </label>
          <label className="space-y-1 text-slate-400">
            Workouts / week
            <input
              type="number"
              min={1}
              max={50}
              value={workoutsDraft}
              onChange={(e) => setWorkoutsDraft(e.target.value)}
              aria-label="Workouts per week goal"
              className="glass-input px-2 py-1 text-xs w-20 block"
            />
          </label>
          <button type="button" className="glass-button px-3 py-1.5 text-xs" onClick={() => void saveGoals()}>
            Save
          </button>
          <button type="button" className="glass-button px-3 py-1.5 text-xs" onClick={() => setEditingGoals(false)}>
            Cancel
          </button>
          <span className="text-[10px] text-slate-500">
            Daily goal ({goals.daily_steps.toLocaleString()}) is edited on the steps card.
          </span>
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-slate-500">Loading achievements…</p>
      ) : (
        <>
          {nextUp.length > 0 && (
            <div className="space-y-2" data-testid="next-up">
              {nextUp.slice(0, 3).map((item) => (
                <div key={item.id} className="space-y-1">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-300">{item.name}</span>
                    <span className="text-slate-500">
                      {Math.round(item.current).toLocaleString()} / {Math.round(item.target).toLocaleString()}
                      {' · '}
                      {Math.round(item.remaining).toLocaleString()} to go
                    </span>
                  </div>
                  <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-amber-400 to-purple-400"
                      style={{ width: `${item.percent}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          {earned.length > 0 ? (
            <div className="flex flex-wrap gap-1.5" data-testid="earned-badges">
              {earned.slice(0, 12).map((badge) => (
                <span
                  key={badge.id}
                  title={`${badge.description} · ${badge.earned_on}`}
                  className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-200 border border-amber-500/25"
                >
                  <Award size={11} />
                  {badge.name}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              No badges yet — your first recorded steps or workout unlocks one.
            </p>
          )}
        </>
      )}
    </div>
  );
}

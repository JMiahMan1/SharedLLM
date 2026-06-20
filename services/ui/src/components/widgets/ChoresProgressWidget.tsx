import { useMemo, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useWidgetData } from '../../hooks/useWidgetData';
import { WidgetCard } from './WidgetCard';
import { api } from '../../services/api';
import type { ChoreItem } from '../../types/widget';
import toast from 'react-hot-toast';

const isChoreAssignedToUser = (chore: ChoreItem, username: string): boolean => {
  if (!chore.assignees || chore.assignees.length === 0) {
    return false;
  }
  return chore.assignees.some(
    (assignee) => assignee.toLowerCase() === username.toLowerCase()
  );
};

const ChoresProgressWidget = () => {
  const { user } = useAuth();
  const [completedOverrides, setCompletedOverrides] = useState<Record<string, boolean>>({});
  const [completingIds, setCompletingIds] = useState<Set<string>>(new Set());

  const fetchChores = async () => {
    if (!user?.username) {
      throw new Error('No user logged in');
    }

    const resp = await api.getSkylightChores(user.username, 'today') as {
      status: string;
      message?: string;
      chores?: ChoreItem[];
    };

    if (resp.status !== 'SUCCESS') {
      throw new Error(resp.message || 'Failed to fetch chores');
    }

    const assigned = (resp.chores || []).filter((chore) =>
      isChoreAssignedToUser(chore, user.username)
    );

    return assigned;
  };

  const { data: chores = [], isLoading, error, refetch } = useWidgetData<ChoreItem[]>(
    ['skylight-chores', user?.username || ''],
    fetchChores,
    300000 // 5 minutes
  );



  const localChores = useMemo(() => {
    return chores.map((chore) => {
      if (chore.id in completedOverrides) {
        return { ...chore, completed: completedOverrides[chore.id] };
      }
      return chore;
    });
  }, [chores, completedOverrides]);

  const handleToggleComplete = async (chore: ChoreItem) => {
    if (!user?.username) return;
    if (!chore.assignees?.some((a) => a.toLowerCase() === user.username!.toLowerCase())) {
      toast.error('This chore is not assigned to you');
      return;
    }

    const newStatus = !chore.completed;
    setCompletedOverrides((prev) => ({ ...prev, [chore.id]: newStatus }));
    setCompletingIds((prev) => new Set(prev).add(chore.id));

    try {
      const resp = newStatus
        ? await api.completeSkylightChore(chore.id)
        : await api.uncompleteSkylightChore(chore.id);

      if (resp.status !== 'SUCCESS') {
        setCompletedOverrides((prev) => ({ ...prev, [chore.id]: !newStatus }));
        toast.error(resp.message || 'Failed to update chore');
      } else {
        refetch();
      }
    } catch {
      setCompletedOverrides((prev) => ({ ...prev, [chore.id]: !newStatus }));
      toast.error('Failed to sync chore state');
    } finally {
      setCompletingIds((prev) => {
        const next = new Set(prev);
        next.delete(chore.id);
        return next;
      });
    }
  };

  const completedCount = localChores.filter((c) => c.completed).length;
  const totalCount = localChores.length;
  const progress = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;
  const allCompleted = totalCount > 0 && completedCount === totalCount;

  return (
    <WidgetCard
      title="Today's Chores"
      isLoading={isLoading}
      error={error}
      onRetry={refetch}
      actions={
        <span className="text-xs text-slate-400">
          {completedCount}/{totalCount}
        </span>
      }
    >
      {totalCount === 0 ? (
        <div className="flex flex-col items-center justify-center text-center h-full">
          <p className="text-sm text-slate-400">No chores assigned</p>
          <p className="text-xs text-slate-500">All clear for today!</p>
        </div>
      ) : (
        <div className="flex flex-col h-full justify-between">
          <div className="relative w-20 h-20 mx-auto mb-5 shrink-0">
            <svg className="w-20 h-20 -rotate-90" viewBox="0 0 36 36">
              <path
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                fill="none"
                stroke="rgba(71, 85, 105, 0.3)"
                strokeWidth="3"
              />
              <path
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                fill="none"
                stroke="url(#progressGradient)"
                strokeWidth="3"
                strokeDasharray={`${progress}, 100`}
                strokeLinecap="round"
                className="transition-all duration-500"
              />
              <defs>
                <linearGradient id="progressGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#10b981" />
                  <stop offset="50%" stopColor="#06b6d4" />
                  <stop offset="100%" stopColor="#8b5cf6" />
                </linearGradient>
              </defs>
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-lg font-bold text-white">{Math.round(progress)}%</span>
            </div>
          </div>

          {allCompleted && (
            <div className="text-center mb-4 shrink-0">
              <span className="text-xs text-emerald-400 font-semibold">All chores completed! &#127881;</span>
            </div>
          )}

          <div className="space-y-2 max-h-48 overflow-y-auto pr-1 flex-1 min-h-0">
            {localChores.map((chore) => {
              const isAssignedToMe = chore.assignees?.some(
                (a) => a.toLowerCase() === user?.username?.toLowerCase()
              );

              return (
                <button
                  key={chore.id}
                  onClick={() => {
                    if (isAssignedToMe) {
                      handleToggleComplete(chore);
                    }
                  }}
                  disabled={!isAssignedToMe || completingIds.has(chore.id)}
                  className={`w-full flex items-center gap-3 p-3 rounded-lg transition-all ${
                    isAssignedToMe
                      ? chore.completed
                        ? 'bg-emerald-500/10 border border-emerald-500/30'
                        : 'bg-slate-800/50 border border-slate-700/50 hover:border-slate-600/50'
                      : 'bg-slate-900/30 border border-slate-800/30 opacity-50 cursor-not-allowed'
                  }`}
                >
                  <div
                    className={`w-5 h-5 rounded-md border-2 flex items-center justify-center shrink-0 transition-colors ${
                      chore.completed
                        ? 'bg-emerald-500 border-emerald-500'
                        : isAssignedToMe
                          ? 'border-slate-500'
                          : 'border-slate-700'
                    }`}
                  >
                    {chore.completed && (
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </div>

                  <div className="flex-1 text-left min-w-0">
                    <p
                      className={`text-sm truncate ${
                        chore.completed ? 'text-slate-400 line-through' : 'text-white'
                      }`}
                    >
                      {chore.title}
                    </p>
                    {chore.stars && chore.stars > 0 && (
                      <p className="text-xs text-yellow-400">
                        &#11088; {chore.stars} star{chore.stars > 1 ? 's' : ''}
                      </p>
                    )}
                  </div>

                  {!isAssignedToMe && (
                    <span className="text-xs text-slate-600 shrink-0">others</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </WidgetCard>
  );
};

export default ChoresProgressWidget;

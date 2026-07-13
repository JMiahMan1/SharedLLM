import { useMemo, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useWidgetData } from '../../hooks/useWidgetData';
import { WidgetCard } from './WidgetCard';
import { api } from '../../services/api';
import type { ChoreItem, IWidgetProps } from '../../types/widget';
import toast from 'react-hot-toast';

const ChoresProgressWidget = ({ settingsButton }: IWidgetProps) => {
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

    // Each chore carries its assignee (the Skylight `category` label, i.e. the
    // family member's name). The gateway scopes the result to the logged-in
    // user (admins get the whole family frame), and any member can toggle.
    return resp.chores || [];
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

  // Group chores by their assignee (Skylight `category` label). When there is
  // only a single assignee (or none), we render a flat list instead.
  const assigneeGroups = useMemo(() => {
    const groups: Record<string, ChoreItem[]> = {};
    for (const chore of localChores) {
      const key =
        chore.assignees && chore.assignees.length > 0
          ? chore.assignees.join(', ')
          : 'Unassigned';
      if (!groups[key]) groups[key] = [];
      groups[key].push(chore);
    }
    return groups;
  }, [localChores]);

  const shouldGroupByUser = Object.keys(assigneeGroups).length > 1;

  const handleToggleComplete = async (chore: ChoreItem) => {
    if (!user?.username) return;

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
        // Server is now authoritative; drop the optimistic override so the UI
        // can't get stuck out of sync if the chore changes elsewhere.
        setCompletedOverrides((prev) => {
          const next = { ...prev };
          delete next[chore.id];
          return next;
        });
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

  const renderChoreButton = (chore: ChoreItem) => (
    <button
      key={chore.id}
      onClick={() => handleToggleComplete(chore)}
      disabled={completingIds.has(chore.id)}
      className={`w-full flex items-center gap-3 p-3 rounded-lg transition-all ${
        chore.completed
          ? 'bg-emerald-500/10 border border-emerald-500/30'
          : 'bg-slate-800/50 border border-slate-700/50 hover:border-slate-600/50'
      }`}
    >
      <div
        className={`w-5 h-5 rounded-md border-2 flex items-center justify-center shrink-0 transition-colors ${
          chore.completed ? 'bg-emerald-500 border-emerald-500' : 'border-slate-500'
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
          {chore.emoji_icon ? `${chore.emoji_icon} ` : ''}
          {chore.title}
        </p>
        {chore.reward ? (
          <p className="text-xs text-yellow-400">
            &#11088; {chore.reward} point{chore.reward > 1 ? 's' : ''}
          </p>
        ) : null}
      </div>
    </button>
  );

  const renderContent = (expanded: boolean) =>
    totalCount === 0 ? (
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

        {/* Compact mode caps the list and scrolls internally; expanded (full-screen)
             mode drops the cap so every chore is visible and the overlay scrolls.
             When chores span multiple assignees, the expanded view groups them by
             user so each person's progress is shown; a single assignee falls back
             to the flat list. */}
        {expanded && shouldGroupByUser ? (
          <div className="flex-1 min-h-0 overflow-y-auto pr-1 space-y-4">
            {Object.entries(assigneeGroups).map(([label, groupChores]) => {
              const done = groupChores.filter((c) => c.completed).length;
              const pct = groupChores.length
                ? Math.round((done / groupChores.length) * 100)
                : 0;
              return (
                <div key={label}>
                  <div className="flex items-center justify-between mb-2 px-1">
                    <span className="text-sm font-semibold text-slate-200">{label}</span>
                    <span className="text-xs text-slate-400">
                      {done}/{groupChores.length} · {pct}%
                    </span>
                  </div>
                  <div className="space-y-2 pr-1">{groupChores.map(renderChoreButton)}</div>
                </div>
              );
            })}
          </div>
        ) : (
          <div
            className={`space-y-2 pr-1 flex-1 min-h-0 ${
              expanded ? '' : 'max-h-48 overflow-y-auto'
            }`}
          >
            {localChores.map(renderChoreButton)}
          </div>
        )}
      </div>
    );

  return (
    <WidgetCard
      title="Today's Chores"
      isLoading={isLoading}
      error={error}
      onRetry={refetch}
      settingsButton={settingsButton}
      isExpandable={true}
      icon="🧹"
      actions={
        <span className="text-xs text-slate-400">
          {completedCount}/{totalCount} done · {Math.round(progress)}%
        </span>
      }
      expandedChildren={renderContent(true)}
    >
      {renderContent(false)}
    </WidgetCard>
  );
};

export default ChoresProgressWidget;

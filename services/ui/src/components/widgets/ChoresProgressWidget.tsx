import { useMemo, useState } from 'react';
import { Check, Star, Sun, Moon } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useWidgetData } from '../../hooks/useWidgetData';
import { useDarkModeSync } from '../../hooks/useDarkModeSync';
import { WidgetCard } from './WidgetCard';
import { api } from '../../services/api';
import type { ChoreItem, IWidgetProps } from '../../types/widget';
import toast from 'react-hot-toast';

type ChoresPayload = {
  chores: ChoreItem[];
  assignee_meta?: Record<string, string>;
};

/* Palette driven by the app's light/dark selection.
   Light = OpenSkyLight "paper planner" warm palette (matches the screenshot).
   Dark  = SharedLLM's existing dark theme tokens (blends with the rest of the UI). */
const OSK_LIGHT = `
  --osk-paper:#f5efe3;
  --osk-paper-deep:#ece4d2;
  --osk-card:#fffdf8;
  --osk-ink:#34302a;
  --osk-ink-soft:#756d5f;
  --osk-ink-faint:#a89f8d;
  --osk-line:#e3dac6;
  --osk-ember:#d95b3a;
  --osk-ember-deep:#bf4526;
  --osk-ember-soft:#f8ddd2;
  --osk-sun:#ffd9a0;
  --osk-sun-soft:#fdf0da;
  --osk-shadow:0 1px 3px rgba(72,60,38,0.07),0 10px 28px -10px rgba(72,60,38,0.16);
`;
/* Dark mode reuses the SharedLLM dark theme (deep navy base, translucent
   surfaces, slate text, purple accent) so the widget blends with the UI. */
const OSK_DARK = `
  --osk-paper: var(--color-bg-base);
  --osk-paper-deep: var(--color-surface-1);
  --osk-card: var(--color-surface-0);
  --osk-ink: #f1f5f9;
  --osk-ink-soft: #cbd5e1;
  --osk-ink-faint: #94a3b8;
  --osk-line: var(--color-border-mid);
  --osk-ember: #8b5cf6;
  --osk-ember-deep: #c4b5fd;
  --osk-ember-soft: rgba(139, 92, 246, 0.18);
  --osk-sun: #fbbf24;
  --osk-sun-soft: rgba(251, 191, 36, 0.16);
  --osk-shadow: var(--shadow-panel);
`;

/** Readable text color (ink or paper) for a given background hex. */
function textOn(hex?: string): string {
  if (!hex) return '#34302a';
  const h = hex.replace('#', '');
  if (h.length < 6) return '#34302a';
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.6 ? '#34302a' : '#fffdf8';
}

/** Deterministic fallback color when Skylight provides no category color. */
function colorForName(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 62%, 55%)`;
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');
}

/** Heuristic routine grouping from the chore's start time (mirrors OpenSkyLight). */
function routineOf(chore: ChoreItem): 'morning' | 'evening' | 'anytime' {
  const t = chore.start_time;
  if (!t) return 'anytime';
  const h = parseInt(t.split(':')[0], 10);
  if (Number.isNaN(h)) return 'anytime';
  if (h < 12) return 'morning';
  if (h >= 17) return 'evening';
  return 'anytime';
}

function StarBadge({ count }: { count: number }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-sm font-extrabold"
      style={{ background: 'var(--osk-sun-soft)', color: 'var(--osk-ember-deep)' }}
    >
      <Star size={14} aria-hidden />
      {count}
    </span>
  );
}

const ChoresProgressWidget = ({ settingsButton }: IWidgetProps) => {
  const { user } = useAuth();
  const { isDark } = useDarkModeSync();
  const [completedOverrides, setCompletedOverrides] = useState<Record<string, boolean>>({});
  const [completingIds, setCompletingIds] = useState<Set<string>>(new Set());

  const fetchChores = async (): Promise<ChoresPayload> => {
    if (!user?.username) {
      throw new Error('No user logged in');
    }

    const resp = (await api.getSkylightChores(user.username, 'today')) as {
      status: string;
      message?: string;
      chores?: ChoreItem[];
      assignee_meta?: Record<string, string>;
    };

    if (resp.status !== 'SUCCESS') {
      throw new Error(resp.message || 'Failed to fetch chores');
    }

    // Each chore carries its assignee (the Skylight `category` label, i.e. the
    // family member's name) and that category's color. The gateway scopes the
    // result to the logged-in user (admins get the whole family frame), and any
    // member can toggle.
    return { chores: resp.chores || [], assignee_meta: resp.assignee_meta || {} };
  };

  const { data = { chores: [], assignee_meta: {} }, isLoading, error, refetch } =
    useWidgetData<ChoresPayload>(['skylight-chores', user?.username || ''], fetchChores, 300000);

  const assigneeMeta = data.assignee_meta ?? {};

  const localChores = useMemo(() => {
    const chores = data.chores ?? [];
    return chores.map((chore) => {
      if (chore.id in completedOverrides) {
        return { ...chore, completed: completedOverrides[chore.id] };
      }
      return chore;
    });
  }, [data.chores, completedOverrides]);

  // Group chores by their assignee (Skylight `category` label).
  const assigneeGroups = useMemo(() => {
    const groups: Record<string, ChoreItem[]> = {};
    for (const chore of localChores) {
      const key =
        chore.assignees && chore.assignees.length > 0 ? chore.assignees.join(', ') : 'Unassigned';
      if (!groups[key]) groups[key] = [];
      groups[key].push(chore);
    }
    return groups;
  }, [localChores]);

  const colorForAssignee = (name: string) => assigneeMeta[name] || colorForName(name);

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

  const renderExpandedChore = (chore: ChoreItem, color: string) => (
    <button
      key={chore.id}
      type="button"
      onClick={() => handleToggleComplete(chore)}
      disabled={completingIds.has(chore.id)}
      className="flex w-full items-center gap-3 rounded-2xl p-3 text-left shadow-[var(--osk-shadow)] transition disabled:opacity-60"
      style={{ background: 'var(--osk-card)' }}
    >
      <span
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border-[3px] transition-colors"
        style={{ borderColor: color, backgroundColor: chore.completed ? color : 'transparent' }}
      >
        {chore.completed && <Check size={24} style={{ color: textOn(color) }} />}
      </span>
      <span
        className={`min-w-0 flex-1 truncate text-lg font-bold ${chore.completed ? 'line-through' : ''}`}
        style={{ color: chore.completed ? 'var(--osk-ink-faint)' : 'var(--osk-ink)' }}
      >
        {chore.emoji_icon ? `${chore.emoji_icon} ` : ''}
        {chore.title}
      </span>
      {chore.reward ? <StarBadge count={chore.reward} /> : null}
    </button>
  );

  const renderPersonColumn = (name: string, groupChores: ChoreItem[], color: string) => {
    const doneCount = groupChores.filter((c) => c.completed).length;
    const earned = groupChores
      .filter((c) => c.completed)
      .reduce((sum, c) => sum + (c.reward || 0), 0);
    const groups = [
      { key: 'morning', label: 'Morning', Icon: Sun, items: groupChores.filter((c) => routineOf(c) === 'morning') },
      { key: 'anytime', label: null, Icon: null, items: groupChores.filter((c) => routineOf(c) === 'anytime') },
      { key: 'evening', label: 'Evening', Icon: Moon, items: groupChores.filter((c) => routineOf(c) === 'evening') },
    ].filter((g) => g.items.length > 0);

    return (
      <div
        key={name}
        className="flex w-full shrink-0 flex-col rounded-[1.25rem] p-3 md:w-80 md:shrink-0"
        style={{ background: 'var(--osk-paper-deep)' }}
      >
        <div className="mb-3 flex items-center gap-3">
          <span
            className="flex h-12 w-12 items-center justify-center rounded-full text-base font-extrabold"
            style={{ background: color, color: textOn(color) }}
          >
            {initials(name)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xl font-bold" style={{ color: 'var(--osk-ink)' }}>
              {name}
            </div>
            <div className="text-sm font-bold" style={{ color: 'var(--osk-ink-faint)' }}>
              {doneCount}/{groupChores.length} done
            </div>
          </div>
          <StarBadge count={earned} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
          {groups.map((g) => (
            <div key={g.key} className="flex flex-col gap-2">
              {g.label && (
                <div
                  className="mt-1 flex items-center gap-1.5 px-1 text-sm font-extrabold uppercase tracking-wide"
                  style={{ color: 'var(--osk-ink-faint)' }}
                >
                  {g.Icon ? <g.Icon size={15} /> : null}
                  {g.label}
                </div>
              )}
              {g.items.map((c) => renderExpandedChore(c, color))}
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderExpanded = () => {
    if (totalCount === 0) {
      return (
        <div className="osk-chores flex h-full flex-col items-center justify-center text-center">
          <p className="text-lg font-semibold" style={{ color: 'var(--osk-ink)' }}>
            No chores assigned
          </p>
          <p className="text-sm" style={{ color: 'var(--osk-ink-faint)' }}>
            All clear for today!
          </p>
        </div>
      );
    }

    const names = Object.keys(assigneeGroups);
    return (
      <>
        <style>{`.osk-overlay{${isDark ? OSK_DARK : OSK_LIGHT}--wc-ink:var(--osk-ink);--wc-ink-faint:var(--osk-ink-faint);--wc-ink-strong:var(--osk-ink);--wc-border:var(--osk-line);--wc-hover:var(--osk-paper-deep);background:var(--osk-paper)!important;}`}</style>
        <div className="osk-chores flex h-full flex-col">
          <div className="mb-4 flex shrink-0 items-end gap-4">
            <div className="flex-1">
              <div className="text-3xl font-bold" style={{ color: 'var(--osk-ink)' }}>
                Today&apos;s Chores
              </div>
            </div>
            <div className="text-sm font-bold" style={{ color: 'var(--osk-ink-faint)' }}>
              {completedCount}/{totalCount} done
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-wrap content-start items-start gap-4 overflow-y-auto pb-2">
            {names.map((name) => renderPersonColumn(name, assigneeGroups[name], colorForAssignee(name)))}
          </div>
        </div>
      </>
    );
  };

  const renderCompactChore = (chore: ChoreItem) => (
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
        <p className={`text-sm truncate ${chore.completed ? 'text-slate-400 line-through' : 'text-white'}`}>
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

  const renderCompact = () =>
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

        <div className="space-y-2 pr-1 flex-1 min-h-0 max-h-48 overflow-y-auto">
          {localChores.map(renderCompactChore)}
        </div>
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
      expandedClassName="osk-overlay"
      actions={
        <span className="text-xs" style={{ color: 'var(--wc-ink-faint, #94a3b8)' }}>
          {completedCount}/{totalCount} done · {Math.round(progress)}%
        </span>
      }
      expandedChildren={renderExpanded()}
    >
      {renderCompact()}
    </WidgetCard>
  );
};

export default ChoresProgressWidget;

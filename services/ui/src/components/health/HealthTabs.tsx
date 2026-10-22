import { Activity, Moon, Sun, UserRound, Utensils } from 'lucide-react';

export type HealthTab = 'today' | 'fitness' | 'sleep' | 'nutrition' | 'health';

const TABS: Array<{
  id: HealthTab;
  label: string;
  icon: typeof Sun;
}> = [
  { id: 'today', label: 'Today', icon: Sun },
  { id: 'fitness', label: 'Fitness', icon: Activity },
  { id: 'sleep', label: 'Sleep', icon: Moon },
  { id: 'nutrition', label: 'Nutrition', icon: Utensils },
  { id: 'health', label: 'Health', icon: UserRound },
];

interface Props {
  value: HealthTab;
  onChange: (tab: HealthTab) => void;
}

/**
 * One tab strip for the whole hub, including the existing Fitness page.
 *
 * The old page had no navigation because it was one surface; adding tabs here
 * rather than a second route means every tab shares the same header, the same
 * data freshness and the phone's back behaviour.
 */
export default function HealthTabs({ value, onChange }: Props) {
  return (
    <div
      role="tablist"
      aria-label="Health sections"
      data-testid="health-tabs"
      className="flex gap-1 overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.03] p-1"
    >
      {TABS.map((tab) => {
        const Icon = tab.icon;
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`health-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`health-panel-${tab.id}`}
            data-testid={`health-tab-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={`flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold transition-colors pointer-coarse:min-h-11 ${
              selected
                ? 'bg-purple-500/20 text-purple-200 border border-purple-500/30'
                : 'border border-transparent text-slate-400 hover:text-white'
            }`}
          >
            <Icon size={15} />
            <span className="whitespace-nowrap">{tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}

import { useState } from 'react';
import { HeartPulse } from 'lucide-react';
import HealthTabs, { type HealthTab } from '../components/health/HealthTabs';
import HealthToday from '../components/health/HealthToday';
import HealthSleep from '../components/health/HealthSleep';
import HealthNutrition from '../components/health/HealthNutrition';
import HealthProfilePanel from '../components/health/HealthProfilePanel';
import HealthCycle from '../components/health/HealthCycle';
import DigitalWellbeingCard from '../components/health/DigitalWellbeingCard';
import ScreenTimeCard from '../components/health/ScreenTimeCard';
import HealthFitness from './HealthFitness';

/**
 * The Health hub.
 *
 * Today is the interpreting surface (readiness, sleep, food, water, steps);
 * Fitness is the existing steps/workouts page, unchanged in behaviour and
 * simply hosted under a tab instead of a second route. The old page is reused
 * rather than rewritten so its hourly charts, workout tracking and family
 * feed keep one implementation.
 */
const Health = () => {
  const [tab, setTab] = useState<HealthTab>('today');

  return (
    <div className="mx-auto max-w-6xl space-y-3 px-1 pb-12 sm:space-y-4 sm:px-0" data-testid="health-page">
      <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5">
        <div className="flex items-center gap-3">
          <div className="shrink-0 rounded-xl border border-purple-500/20 bg-purple-500/10 p-2.5">
            <HeartPulse size={24} className="text-purple-400" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold text-white sm:text-2xl">Health</h1>
            <p className="mt-0.5 text-xs text-slate-400">
              Readiness, sleep, food and the activity your phone records — one day, one screen.
            </p>
          </div>
        </div>
      </header>

      <HealthTabs value={tab} onChange={setTab} />

      <div
        role="tabpanel"
        id={`health-panel-${tab}`}
        aria-labelledby={`health-tab-${tab}`}
        className="pt-1"
      >
        {tab === 'today' && (
          <div className="space-y-3 sm:space-y-4">
            <ScreenTimeCard />
            <HealthToday onNavigate={setTab} />
            <DigitalWellbeingCard />
          </div>
        )}
        {tab === 'fitness' && <HealthFitness />}
        {tab === 'sleep' && <HealthSleep />}
        {tab === 'nutrition' && <HealthNutrition onNavigate={setTab} />}
        {tab === 'health' && (
          <div className="space-y-3 sm:space-y-4">
            <HealthProfilePanel />
            {/* Opt-in, private, and hidden entirely for a male profile. */}
            <HealthCycle />
          </div>
        )}
      </div>
    </div>
  );
};

export default Health;

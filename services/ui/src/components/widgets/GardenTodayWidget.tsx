import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Flower, AlertCircle, Clock, Scissors } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import WidgetCard from './WidgetCard';import { api } from '../../services/api';

/**
 * Garden hub today card on the dashboard. Shows due plant count, overdue
 * count, and a quick jump to /garden. Matches HealthTodayWidget conventions:
 * WidgetCard wrapper, lucide-react icons, @tanstack/react-query useQuery,
 * 44 px minimum touch targets (pointer-coarse:min-h-11).
 */
export default function GardenTodayWidget({ settingsButton }: IWidgetProps) {
  const navigate = useNavigate();
  const todayQuery = useQuery({
    queryKey: ['garden-today-widget'],
    queryFn: () => api.gardenToday(),
    staleTime: 60_000,
    retry: false,
  });
  const today = todayQuery.data;

  const dueCount = today?.due_count ?? 0;
  const overdueCount = today?.overdue_count ?? 0;
  const readyCount = today?.ready_to_harvest?.length ?? 0;

  return (
    <WidgetCard
      title="Garden today"
      icon={<Flower size={16} />}
      settingsButton={settingsButton}
      isLoading={todayQuery.isLoading}
      error={todayQuery.isError ? 'The garden store could not be reached.' : null}
      onRetry={() => void todayQuery.refetch()}
    >
      <button
        type="button"
        onClick={() => navigate('/garden')}
        data-testid="garden-today-widget"
        className="flex h-full w-full flex-col gap-2 rounded-xl p-1 text-left pointer-coarse:min-h-11"
      >
        {dueCount === 0 && overdueCount === 0 && readyCount === 0 ? (
          <div className="flex items-center gap-2">
            <Flower size={14} className="text-emerald-400" />
            <span className="text-sm font-semibold text-emerald-400">All plants watered</span>
          </div>
        ) : (
          <>
            {overdueCount > 0 && (
              <div className="flex items-center gap-2 mb-1">
                <AlertCircle size={14} className="text-red-400" />
                <span className={`text-sm font-semibold ${overdueCount > 1 ? 'text-red-400' : 'text-amber-400'}`}>
                  {overdueCount} overdue
                </span>
              </div>
            )}
            {dueCount > 0 && (
              <div className="flex items-center gap-2">
                <Clock size={14} className="text-emerald-400" />
                <span className="text-sm font-semibold text-emerald-400">{dueCount} due today</span>
              </div>
            )}
            {readyCount > 0 && (
              <div className="flex items-center gap-2 mb-1">
                <Scissors size={14} className="text-emerald-400" />
                <span className="text-sm font-semibold text-emerald-400">{readyCount} ready to harvest</span>
              </div>
            )}
          </>
        )}
      </button>
    </WidgetCard>
  );
}

import { useEffect, useCallback, lazy, Suspense } from 'react';
import { useWidgetStore } from '../stores/widgetStore';
import WidgetContextMenu from '../widgets/WidgetContextMenu';
import type { WidgetSize } from '../types/widget';

const SIZE_CLASSES: Record<WidgetSize, { gridCol: string; gridRow: string }> = {
  small: { gridCol: 'col-span-1', gridRow: 'row-span-1' },
  medium: { gridCol: 'col-span-1', gridRow: 'row-span-2' },
  wide: { gridCol: 'col-span-2', gridRow: 'row-span-1' },
  tall: { gridCol: 'col-span-1', gridRow: 'row-span-3' },
};

const LazyWidgets: Record<string, React.LazyExoticComponent<React.ComponentType>> = {
  energy_insights: lazy(() => import('../widgets/EnergyInsightsWidget')),
  ambient_timer: lazy(() => import('../widgets/AmbientTimerWidget')),
  quick_notes: lazy(() => import('../widgets/QuickNotesWidget')),
  active_media: lazy(() => import('../widgets/ActiveMediaWidget')),
  chores_progress: lazy(() => import('../widgets/ChoresProgressWidget')),
  upcoming_events: lazy(() => import('../widgets/UpcomingEventsWidget')),
  quick_assistant: lazy(() => import('../widgets/QuickAssistantWidget')),
  device_control: lazy(() => import('../widgets/DeviceControlWidget')),
};

const WidgetSkeleton = () => (
  <div className="glass-card h-full p-5 flex items-center justify-center">
    <div className="animate-pulse text-sm text-slate-500">Loading...</div>
  </div>
);

const BentoBoxDashboard = () => {
  const widgets = useWidgetStore((s) => s.getVisibleWidgets());
  const syncWithServer = useWidgetStore((s) => s.syncWithServer);

  const handleToggleVisibility = useCallback((widgetKey: string, visible: boolean) => {
    if (visible) {
      useWidgetStore.getState().showWidget(widgetKey as never);
    } else {
      useWidgetStore.getState().hideWidget(widgetKey as never);
    }
  }, []);

  const handleTogglePin = useCallback((widgetKey: string) => {
    useWidgetStore.getState().togglePin(widgetKey as never);
  }, []);

  const handleResize = useCallback((widgetKey: string, size: WidgetSize) => {
    useWidgetStore.getState().updateSize(widgetKey as never, size);
  }, []);

  const handleReorder = useCallback((widgetKey: string, newIndex: number) => {
    useWidgetStore.getState().updateOrder(widgetKey as never, newIndex);
  }, []);

  const handleRemove = useCallback((widgetKey: string) => {
    useWidgetStore.getState().removeWidget(widgetKey as never);
  }, []);

  useEffect(() => {
    syncWithServer();
  }, [syncWithServer]);

  const totalWidgets = widgets.length;

  return (
    <div className="space-y-6">
      {widgets.length === 0 ? (
        <div className="glass-panel p-12 text-center">
          <p className="text-lg font-semibold text-white mb-2">No widgets to display</p>
          <p className="text-sm text-slate-400">
            Widgets will appear here when they become available.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {widgets.map((widget) => {
            const LazyWidget = LazyWidgets[widget.def.key];
            const sizeClass = SIZE_CLASSES[widget.userSettings.size] || SIZE_CLASSES.medium;

            return (
              <div
                key={widget.def.key}
                className={`${sizeClass.gridCol} ${sizeClass.gridRow}`}
              >
                <div className="h-full relative">
                  <WidgetContextMenu
                    widgetKey={widget.def.key as never}
                    userSettings={widget.userSettings}
                    def={widget.def}
                    onToggleVisibility={handleToggleVisibility}
                    onTogglePin={handleTogglePin}
                    onResize={handleResize}
                    onReorder={handleReorder}
                    totalWidgets={totalWidgets}
                    onRemove={handleRemove}
                  />
                  <Suspense fallback={<WidgetSkeleton />}>
                    {LazyWidget && <LazyWidget />}
                  </Suspense>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default BentoBoxDashboard;

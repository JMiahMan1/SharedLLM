import { useEffect, useCallback, lazy, Suspense, Component, type ReactNode } from 'react';
import { useWidgetStore } from '../../stores/widgetStore';
import { shallow } from 'zustand/shallow';
import WidgetContextMenu from '../widgets/WidgetContextMenu';
import type { WidgetSize, WidgetKey, IActiveMediaWidgetProps } from '../../types/widget';
import { WidgetSkeletonSelector } from '../widgets/skeletons/WidgetSkeletons';

const SIZE_CLASSES: Record<WidgetSize, { gridCol: string; gridRow: string }> = {
  small: { gridCol: 'col-span-1', gridRow: 'row-span-1' },
  medium: { gridCol: 'col-span-1', gridRow: 'row-span-2' },
  wide: { gridCol: 'col-span-1 md:col-span-2', gridRow: 'row-span-1' },
  tall: { gridCol: 'col-span-1', gridRow: 'row-span-2 md:row-span-3' },
};

const LazyWidgets: Record<WidgetKey, React.LazyExoticComponent<React.ComponentType<IActiveMediaWidgetProps>>> = {
  energy_insights: lazy(() => import('../widgets/EnergyInsightsWidget')),
  ambient_timer: lazy(() => import('../widgets/AmbientTimerWidget')),
  quick_notes: lazy(() => import('../widgets/QuickNotesWidget')),
  active_media: lazy(() => import('../widgets/ActiveMediaWidget')),
  chores_progress: lazy(() => import('../widgets/ChoresProgressWidget')),
  upcoming_events: lazy(() => import('../widgets/UpcomingEventsWidget')),
  quick_assistant: lazy(() => import('../widgets/QuickAssistantWidget')),
  device_control: lazy(() => import('../widgets/DeviceControlWidget')),
};

interface ErrorBoundaryProps {
  children: ReactNode;
  widgetKey: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

class WidgetErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error(`Error in widget ${this.props.widgetKey}:`, error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="glass-panel p-5 flex flex-col items-center justify-center text-center h-full min-h-[150px] bg-red-950/20 border border-red-500/20 rounded-2xl">
          <p className="text-sm font-semibold text-red-400 mb-2">Widget Failed</p>
          <p className="text-xs text-red-300/80 mb-4 max-w-xs">
            Failed to render dynamic widget interface.
          </p>
          <button
            onClick={this.handleReset}
            className="glass-button px-3 py-1.5 text-xs text-red-400 hover:text-red-300 font-semibold"
          >
            Reset Widget
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

const BentoBoxDashboard = () => {
  const widgets = useWidgetStore((s) => s.getVisibleWidgets(), shallow);

  useEffect(() => {
    useWidgetStore.getState().syncWithServer();
  }, []);

  const handleToggleVisibility = useCallback((widgetKey: WidgetKey, visible: boolean) => {
    if (visible) {
      useWidgetStore.getState().showWidget(widgetKey);
    } else {
      useWidgetStore.getState().hideWidget(widgetKey);
    }
  }, []);

  const handleTogglePin = useCallback((widgetKey: WidgetKey) => {
    useWidgetStore.getState().togglePin(widgetKey);
  }, []);

  const handleResize = useCallback((widgetKey: WidgetKey, size: WidgetSize) => {
    useWidgetStore.getState().updateSize(widgetKey, size);
  }, []);

  const handleReorder = useCallback((widgetKey: WidgetKey, newIndex: number) => {
    useWidgetStore.getState().updateOrder(widgetKey, newIndex);
  }, []);

  const handleRemove = useCallback((widgetKey: WidgetKey) => {
    useWidgetStore.getState().removeWidget(widgetKey);
  }, []);

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
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {widgets.map((widget) => {
            const LazyWidget = LazyWidgets[widget.def.key];
            const sizeClass = SIZE_CLASSES[widget.userSettings.size] || SIZE_CLASSES.medium;

            const settingsButton = (
              <WidgetContextMenu
                widgetKey={widget.def.key}
                userSettings={widget.userSettings}
                def={widget.def}
                onToggleVisibility={handleToggleVisibility}
                onTogglePin={handleTogglePin}
                onResize={handleResize}
                onReorder={handleReorder}
                totalWidgets={totalWidgets}
                onRemove={handleRemove}
              />
            );

            const renderWidget = () => {
              if (!LazyWidget) return null;
              const WidgetComponent = LazyWidget;
              return (
                <WidgetComponent
                  settingsButton={settingsButton}
                  userSettings={widget.userSettings}
                  onTogglePin={() => handleTogglePin(widget.def.key)}
                  onMediaStop={() => {}}
                />
              );
            };

            return (
              <div
                key={widget.def.key}
                className={`${sizeClass.gridCol} ${sizeClass.gridRow}`}
              >
                <div className="h-full relative">
                  <WidgetErrorBoundary widgetKey={widget.def.key}>
                    <Suspense fallback={<WidgetSkeletonSelector widgetKey={widget.def.key} />}>
                      {renderWidget()}
                    </Suspense>
                  </WidgetErrorBoundary>
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

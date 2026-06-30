import { useEffect, useCallback, lazy, Suspense, Component, type ReactNode } from 'react';
import { Settings2 } from 'lucide-react';
import { useWidgetStore } from '../../stores/widgetStore';
import { useShallow } from 'zustand/react/shallow';
import WidgetContextMenu from '../widgets/WidgetContextMenu';
import type { WidgetSize, WidgetKey, IActiveMediaWidgetProps } from '../../types/widget';
import { WidgetSkeletonSelector } from '../widgets/skeletons/WidgetSkeletons';

// ── Grid sizing system ────────────────────────────────────────────────────────
// The grid uses auto-rows of 200px. Each size maps to a specific span.
const SIZE_CLASSES: Record<WidgetSize, { col: string; row: string }> = {
  small:  { col: 'col-span-1',               row: 'row-span-1' }, // ~200px tall
  medium: { col: 'col-span-1',               row: 'row-span-2' }, // ~420px tall
  wide:   { col: 'col-span-1 md:col-span-2', row: 'row-span-1' }, // ~200px tall, 2 cols wide
  tall:   { col: 'col-span-1',               row: 'row-span-3' }, // ~620px tall
};

// ── Lazy widget imports ──────────────────────────────────────────────────────
const LazyWidgets: Record<WidgetKey, React.LazyExoticComponent<React.ComponentType<IActiveMediaWidgetProps>>> = {
  energy_insights:  lazy(() => import('../widgets/EnergyInsightsWidget')),
  ambient_timer:    lazy(() => import('../widgets/AmbientTimerWidget')),
  quick_notes:      lazy(() => import('../widgets/QuickNotesWidget')),
  active_media:     lazy(() => import('../widgets/ActiveMediaWidget')),
  chores_progress:  lazy(() => import('../widgets/ChoresProgressWidget')),
  upcoming_events:  lazy(() => import('../widgets/UpcomingEventsWidget')),
  quick_assistant:  lazy(() => import('../widgets/QuickAssistantWidget')),
  device_control:   lazy(() => import('../widgets/DeviceControlWidget')),
};

// ── Widget-level error boundary ──────────────────────────────────────────────
interface WidgetErrorBoundaryProps { children: ReactNode; widgetKey: string }
interface WidgetErrorBoundaryState { hasError: boolean; resetKey: number }

class WidgetErrorBoundary extends Component<WidgetErrorBoundaryProps, WidgetErrorBoundaryState> {
  constructor(props: WidgetErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, resetKey: 0 };
  }

  static getDerivedStateFromError(): Partial<WidgetErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error(`[Widget "${this.props.widgetKey}"] crashed:`, error, errorInfo);
  }

  handleReset = () => {
    this.setState((s) => ({ hasError: false, resetKey: s.resetKey + 1 }));
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="glass-panel h-full flex flex-col items-center justify-center text-center p-6 bg-red-950/15 border-red-500/20 rounded-2xl gap-3">
          <span className="text-2xl">⚠️</span>
          <div>
            <p className="text-sm font-semibold text-red-300">Widget failed</p>
            <p className="text-[10px] text-red-400/60 mt-0.5 capitalize">
              {this.props.widgetKey.replace(/_/g, ' ')}
            </p>
          </div>
          <button
            onClick={this.handleReset}
            className="glass-button px-3 py-1.5 text-xs text-red-300 border-red-500/30 hover:bg-red-500/10 min-h-0"
          >
            Reset
          </button>
        </div>
      );
    }

    return (
      // Re-mount on reset to clear any broken internal state
      <div key={this.state.resetKey} className="h-full">
        {this.props.children}
      </div>
    );
  }
}

// ── BentoBoxDashboard ────────────────────────────────────────────────────────

const NOOP = () => {};

const BentoBoxDashboard = () => {
  const widgets = useWidgetStore(useShallow((state) => state.getVisibleWidgets()));
  const mounting = useWidgetStore((state) => state.mounting);

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

  // Show a gentle loading state while syncing widget settings on first render
  if (mounting && totalWidgets === 0) {
    return (
      <div
        className="grid gap-5"
        style={{
          gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 280px), 1fr))',
          gridAutoRows: '200px',
        }}
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton rounded-2xl" style={{ gridRow: i % 3 === 0 ? 'span 2' : 'span 1' }} />
        ))}
      </div>
    );
  }

  if (widgets.length === 0) {
    return (
      <div className="glass-panel p-12 text-center">
        <Settings2 size={32} className="mx-auto text-slate-700 mb-3" />
        <p className="text-base font-semibold text-white mb-1">No widgets to display</p>
        <p className="text-sm text-slate-500">
          Widgets are hidden or waiting for server configuration.
        </p>
      </div>
    );
  }

  return (
    <div
      className="grid gap-5"
      style={{
        // Responsive: fills width with columns of at least 280px, max 1fr
        gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 280px), 1fr))',
        // Each row is 200px; widgets spanning multiple rows get more height
        gridAutoRows: '200px',
      }}
    >
      {widgets.map((widget) => {
        const LazyWidget = LazyWidgets[widget.def.key];
        const size = (widget.userSettings.size as WidgetSize) || 'medium';
        const sizeClass = SIZE_CLASSES[size] ?? SIZE_CLASSES.medium;

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

        return (
          <div
            key={widget.def.key}
            className={`${sizeClass.col} ${sizeClass.row}`}
          >
            <WidgetErrorBoundary widgetKey={widget.def.key}>
              <Suspense fallback={<WidgetSkeletonSelector widgetKey={widget.def.key} />}>
                {LazyWidget ? (
                  <LazyWidget
                    settingsButton={settingsButton}
                    userSettings={widget.userSettings}
                    onTogglePin={() => handleTogglePin(widget.def.key)}
                    onMediaStop={NOOP}
                  />
                ) : null}
              </Suspense>
            </WidgetErrorBoundary>
          </div>
        );
      })}
    </div>
  );
};

export default BentoBoxDashboard;

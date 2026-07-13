import React, { Component, useState, useEffect, type ReactNode } from 'react';
import { Maximize2, Minimize2, AlertTriangle, RefreshCw } from 'lucide-react';

// ── Error Boundary ──────────────────────────────────────────────────────────

interface ErrorBoundaryProps {
  children: ReactNode;
  widgetTitle?: string;
  onReset?: () => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class WidgetErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error(`[WidgetCard] "${this.props.widgetTitle}" caught an error:`, error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center p-6 text-center h-full rounded-xl bg-red-950/20 border border-red-500/20">
          <AlertTriangle size={24} className="text-red-400 mb-3" />
          <p className="text-sm font-semibold text-red-300 mb-1">Widget Error</p>
          <p className="text-xs text-red-400/70 mb-4 max-w-[200px] break-words">
            {this.state.error?.message || 'An unexpected error occurred.'}
          </p>
          <button
            onClick={this.handleReset}
            className="glass-button px-3 py-1.5 text-xs text-red-300 border-red-500/30 hover:bg-red-500/10"
          >
            <RefreshCw size={12} />
            Reset Widget
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

// ── WidgetCard ──────────────────────────────────────────────────────────────

interface WidgetCardProps {
  title: string;
  isLoading?: boolean;
  error?: Error | string | null;
  onRetry?: () => void;
  actions?: ReactNode;
  icon?: ReactNode;
  settingsButton?: ReactNode;
  isExpandable?: boolean;
  children: ReactNode;
  expandedChildren?: ReactNode;
  accentColor?: string;
  /** Overrides the expanded full-screen overlay background (e.g. for a light theme). */
  expandedClassName?: string;
}

export const WidgetCard: React.FC<WidgetCardProps> = ({
  title,
  isLoading = false,
  error = null,
  onRetry,
  actions,
  icon,
  settingsButton,
  isExpandable = false,
  children,
  expandedChildren,
  accentColor,
  expandedClassName,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [resetKey, setResetKey] = useState(0);

  // Prevent background scrolling when widget is expanded
  useEffect(() => {
    if (isExpanded) {
      document.body.classList.add('overflow-hidden');
    } else {
      document.body.classList.remove('overflow-hidden');
    }
    return () => {
      document.body.classList.remove('overflow-hidden');
    };
  }, [isExpanded]);

  const handleBoundaryReset = () => {
    setResetKey((k) => k + 1);
  };

  const header = (
    <div className="flex items-center justify-between mb-4 shrink-0">
      {/* Left: icon + title */}
      <div className="flex items-center gap-2.5 min-w-0">
        {icon && (
          <span
            className="shrink-0 flex items-center justify-center w-8 h-8 rounded-lg text-base"
            style={accentColor ? { background: `${accentColor}18`, color: accentColor } : undefined}
          >
            {icon}
          </span>
        )}
        <h4 className="text-sm font-bold text-white tracking-wide truncate">{title}</h4>
      </div>

      {/* Right: actions + expand + settings */}
      <div className="flex items-center gap-1.5 shrink-0 relative z-20">
        {actions && <div className="flex items-center gap-1">{actions}</div>}

        {isExpandable && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setIsExpanded(!isExpanded);
            }}
            className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-colors"
            title={isExpanded ? 'Collapse' : 'Expand to full screen'}
            aria-label={isExpanded ? 'Collapse widget' : 'Expand widget'}
          >
            {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        )}

        {settingsButton}
      </div>
    </div>
  );

  const body = (
    <div className="flex-1 min-h-0 relative flex flex-col">
      <WidgetErrorBoundary key={resetKey} widgetTitle={title} onReset={handleBoundaryReset}>
        {isLoading ? (
          // Skeleton shimmer
          <div className="flex flex-col gap-3 py-2">
            <div className="skeleton h-4 w-3/4" />
            <div className="skeleton h-4 w-1/2" />
            <div className="skeleton h-4 w-5/6" />
            <div className="skeleton h-4 w-2/3" />
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center text-center flex-1 gap-3 p-4 min-h-[80px]">
            <AlertTriangle size={20} className="text-amber-400/70" />
            <p className="text-xs text-slate-400 break-words max-w-[220px]">
              {typeof error === 'string' ? error : error.message || 'Failed to load widget data'}
            </p>
            {onRetry && (
              <button
                onClick={onRetry}
                className="glass-button px-3 py-1.5 text-xs font-semibold"
              >
                <RefreshCw size={12} />
                Retry
              </button>
            )}
          </div>
        ) : (
          <div className="flex-1 min-h-0 overflow-y-auto">
            {isExpanded && expandedChildren ? expandedChildren : children}
          </div>
        )}
      </WidgetErrorBoundary>
    </div>
  );

  // ── Expanded (full-screen overlay) ──────────────────────────────────────

  if (isExpanded) {
    return (
      <div className={`fixed inset-0 z-50 flex flex-col p-4 md:p-8 animate-fade-up ${expandedClassName ?? 'bg-slate-950/98 backdrop-blur-xl'}`}>
        {/* Close strip */}
        <div className="flex items-center justify-between mb-6 max-w-7xl mx-auto w-full shrink-0">
          <div className="flex items-center gap-3">
            {icon && (
              <span
                className="shrink-0 flex items-center justify-center w-10 h-10 rounded-xl text-lg"
                style={accentColor ? { background: `${accentColor}20`, color: accentColor } : { background: 'rgba(139,92,246,0.12)', color: '#c4b5fd' }}
              >
                {icon}
              </span>
            )}
            <h2 className="text-xl font-bold" style={{ color: 'var(--wc-ink, #ffffff)' }}>{title}</h2>
          </div>

          <div className="flex items-center gap-2">
            {actions}
            <button
              onClick={() => setIsExpanded(false)}
              className="wc-overlay-close p-2 rounded-xl border transition-colors"
              aria-label="Collapse widget"
            >
              <Minimize2 size={16} />
            </button>
            {settingsButton}
          </div>
        </div>

        <div className="flex-1 max-w-7xl mx-auto w-full min-h-0 overflow-y-auto">
          <WidgetErrorBoundary key={resetKey} widgetTitle={title} onReset={handleBoundaryReset}>
            {isLoading ? (
              <div className="flex flex-col gap-4 py-4">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="skeleton h-16 w-full rounded-xl" />
                ))}
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center text-center gap-4 py-12">
                <AlertTriangle size={32} className="text-amber-400/70" />
                <p className="text-slate-400 max-w-sm">
                  {typeof error === 'string' ? error : error.message || 'Failed to load data'}
                </p>
                {onRetry && (
                  <button onClick={onRetry} className="glass-button px-5 py-2.5 font-semibold">
                    <RefreshCw size={14} />
                    Retry
                  </button>
                )}
              </div>
            ) : (
              expandedChildren ?? children
            )}
          </WidgetErrorBoundary>
        </div>
      </div>
    );
  }

  // ── Compact card ─────────────────────────────────────────────────────────

  return (
    <div
      className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative transition-all duration-300"
      style={accentColor ? { '--tw-ring-color': accentColor } as React.CSSProperties : undefined}
    >
      {header}
      {body}
    </div>
  );
};

export default WidgetCard;

import React, { Component, useState, useEffect, type ReactNode } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';

// Error Boundary implementation
interface ErrorBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
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
    console.error("WidgetErrorBoundary caught an error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

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
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

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

  const fallbackUI = (
    <div className="flex flex-col items-center justify-center p-6 text-center h-full bg-red-950/20 border border-red-500/20 rounded-2xl">
      <p className="text-sm font-semibold text-red-400 mb-2">Widget Crashed</p>
      <p className="text-xs text-red-300/80 mb-4 max-w-xs break-words">
        An unexpected error occurred in this widget.
      </p>
      <button
        onClick={() => window.location.reload()}
        className="glass-button px-3 py-1.5 text-xs text-red-400 hover:text-red-300"
      >
        Reload Page
      </button>
    </div>
  );

  const cardContent = (
    <>
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          {icon && <span className="text-slate-400 shrink-0 flex items-center justify-center">{icon}</span>}
          <h4 className="text-sm font-bold text-white tracking-wide truncate">{title}</h4>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 relative z-20">
          {actions}
          {isExpandable && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setIsExpanded(!isExpanded);
              }}
              className="text-slate-400 hover:text-white transition-colors p-1 rounded hover:bg-white/5"
              title={isExpanded ? 'Collapse' : 'Expand'}
            >
              {isExpanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            </button>
          )}
          {settingsButton}
        </div>
      </div>

      <div className="flex-1 min-h-0 relative flex flex-col">
        <WidgetErrorBoundary fallback={fallbackUI}>
          {isLoading ? (
            <div className="flex flex-col gap-3 h-full justify-center">
              <div className="h-4 bg-white/5 rounded w-3/4 animate-pulse" />
              <div className="h-4 bg-white/5 rounded w-1/2 animate-pulse" />
              <div className="h-4 bg-white/5 rounded w-5/6 animate-pulse" />
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center text-center h-full gap-3 p-4">
              <p className="text-xs text-slate-400 break-words max-w-xs">
                {typeof error === 'string' ? error : error.message || 'Failed to load widget data'}
              </p>
              {onRetry && (
                <button
                  onClick={onRetry}
                  className="glass-button px-3 py-1.5 text-xs font-semibold"
                >
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
    </>
  );

  if (isExpanded) {
    return (
      <div className="fixed inset-0 z-50 bg-slate-950/98 p-6 md:p-8 flex flex-col backdrop-blur-md animate-in fade-in zoom-in duration-200">
        <div className="max-w-7xl mx-auto w-full h-full flex flex-col">
          {cardContent}
        </div>
      </div>
    );
  }

  return (
    <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full select-none relative transition-all duration-300 hover:border-white/10">
      {cardContent}
    </div>
  );
};

export default WidgetCard;

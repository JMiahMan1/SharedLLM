import { Component, ErrorInfo, ReactNode, useState } from 'react';
import { 
  ShieldAlert, 
  RefreshCcw, 
  Home, 
  Copy, 
  ChevronDown, 
  ChevronRight, 
  ExternalLink,
  Info
} from 'lucide-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  expandedSections: Set<string>;
}

interface ErrorDetail {
  label: string;
  value: string;
  isExpandable?: boolean;
}

function getErrorDetails(error: Error | null, errorInfo: ErrorInfo | null): ErrorDetail[] {
  const details: ErrorDetail[] = [];
  
  if (error) {
    // Get full error message with name
    const fullMessage = `${error.name || 'Error'}: ${error.message}`;
    details.push({
      label: 'Error Message',
      value: fullMessage,
    });

    // Check for React-specific error codes
    const reactErrorMatch = error.message.match(/Minified React error #(\d+)/);
    if (reactErrorMatch) {
      const errorNum = reactErrorMatch[1];
      details.push({
        label: 'React Error Code',
        value: `#${errorNum} - Visit https://react.dev/errors/${errorNum} for details`,
        isExpandable: true,
      });
    }

    // Stack trace
    if (error.stack) {
      details.push({
        label: 'Stack Trace',
        value: error.stack,
        isExpandable: true,
      });
    }
  }

  // Component stack
  if (errorInfo?.componentStack) {
    details.push({
      label: 'Component Stack',
      value: errorInfo.componentStack,
      isExpandable: true,
    });
  }

  // Additional error properties
  if (error) {
    const extraProps = Object.keys(error).filter(
      key => 
        !['name', 'message', 'stack', 'componentStack'].includes(key) &&
        typeof (error as unknown as Record<string, unknown>)[key] === 'string'
    );
    
    if (extraProps.length > 0) {
      const extraInfo = extraProps
        .map(key => `${key}: ${(error as unknown as Record<string, unknown>)[key]}`)
        .join('\n');
      details.push({
        label: 'Additional Details',
        value: extraInfo,
        isExpandable: true,
      });
    }
  }

  // Environment info
  const envInfo = [
    `URL: ${window.location.href}`,
    `User Agent: ${navigator.userAgent}`,
    `Language: ${navigator.language}`,
    `Platform: ${navigator.platform}`,
    `Screen: ${window.screen.width}x${window.screen.height}`,
    `Time: ${new Date().toISOString()}`,
  ].join('\n');
  
  details.push({
    label: 'Environment',
    value: envInfo,
    isExpandable: true,
  });

  return details;
}

function ErrorSection({ detail }: { detail: ErrorDetail }) {
  const [isExpanded, setIsExpanded] = useState(false);
  
  const handleCopy = () => {
    navigator.clipboard.writeText(detail.value);
    // Could add a toast here, but keeping it simple
  };

  if (!detail.isExpandable) {
    return (
      <div className="mb-3">
        <div className="text-xs text-slate-400 font-medium mb-1 uppercase tracking-wide">
          {detail.label}
        </div>
        <pre className="text-red-400/90 font-mono text-sm bg-black/30 p-3 rounded-lg border border-slate-800 overflow-x-auto whitespace-pre-wrap max-h-32">
          {detail.value}
        </pre>
      </div>
    );
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs text-slate-400 font-medium uppercase tracking-wide">
          {detail.label}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
            title="Copy to clipboard"
          >
            <Copy className="w-3 h-3" />
          </button>
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
            title={isExpanded ? 'Collapse' : 'Expand'}
          >
            {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          </button>
        </div>
      </div>
      {isExpanded && (
        <pre className="text-red-400/90 font-mono text-xs bg-black/30 p-3 rounded-lg border border-slate-800 overflow-x-auto whitespace-pre-wrap max-h-96">
          {detail.value}
        </pre>
      )}
    </div>
  );
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
    expandedSections: new Set(),
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null, expandedSections: new Set() };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught React UI error:', error, errorInfo);
    
    // Check if this is a Vite chunk loading error (often happens after a new deployment)
    if (error.message.includes('dynamically imported module') || error.message.includes('Failed to fetch dynamically imported module')) {
        window.location.reload();
    }

    this.setState({
      error,
      errorInfo,
    });
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null, expandedSections: new Set() });
    window.location.reload();
  };

  private handleGoHome = () => {
    this.setState({ hasError: false, error: null, errorInfo: null, expandedSections: new Set() });
    window.location.href = '/';
  };

  private getErrorSummary(): string {
    const { error } = this.state;
    if (!error) return 'Unknown error occurred';

    const message = error.message;
    
    // Common React error patterns
    if (message.includes('Minified React error #185')) {
      return 'React hooks violation - component called different number of hooks between renders';
    }
    if (message.includes('Minified React error #31')) {
      return 'Component not found - likely a missing export or import error';
    }
    if (message.includes('Minified React error #426')) {
      return 'Invalid hook call - hooks must be called at the top level of a component';
    }
    if (message.includes('Minified React error #170')) {
      return 'Component returned undefined - check component return values';
    }
    if (message.includes('dynamically imported module')) {
      return 'Missing or corrupted JavaScript chunk - check network and rebuild';
    }
    
    return error.name ? `${error.name}: ${error.message}` : error.message;
  }

  public render() {
    const { hasError, error } = this.state;
    
    if (hasError) {
      const details = getErrorDetails(error, this.state.errorInfo);
      const summary = this.getErrorSummary();

      return (
        <div
          role="alert"
          className="min-h-screen flex items-center justify-center bg-slate-950 p-4 font-sans"
        >
          <div className="bg-slate-900 border border-white/10 rounded-xl p-8 max-w-2xl w-full text-center shadow-2xl">
            <ShieldAlert className="w-14 h-14 text-amber-400 mx-auto mb-5" />
            <h1 className="text-2xl font-bold text-white mb-2">Something went wrong</h1>

            <p className="text-slate-400 mb-6">
              Jarvis hit a problem showing this page. Try again - and if it keeps happening, tell a
              grown-up.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <button 
                onClick={this.handleReset}
                className="flex items-center justify-center py-3 px-6 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium flex-1 shadow-lg shadow-blue-900/20"
              >
                <RefreshCcw className="w-5 h-5 mr-2" />
                Try again
              </button>
              
              <button 
                onClick={this.handleGoHome}
                className="flex items-center justify-center py-3 px-6 bg-white/5 hover:bg-white/10 text-slate-200 border border-white/10 rounded-lg transition-colors font-medium flex-1"
              >
                <Home className="w-5 h-5 mr-2" />
                Go home
              </button>
            </div>

            {/* Still here for whoever needs it, but a stack trace is not what a
                child should be handed when a page fails to draw. */}
            <details className="mt-6 text-left">
              <summary className="cursor-pointer select-none text-xs font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-400">
                Technical details
              </summary>

              <p className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
                <Info className="w-3.5 h-3.5" />
                <span>{summary}</span>
              </p>

              <div className="mt-2 space-y-2">
                {details.map((detail) => (
                  <ErrorSection key={detail.label} detail={detail} />
                ))}
              </div>

              <p className="mt-3 text-xs text-slate-500">
                Copy the details above and file an issue at{' '}
                <a 
                  href="https://github.com/JMiahMan1/SharedLLM/issues" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="text-blue-400 hover:text-blue-300 underline inline-flex items-center gap-1"
                >
                  GitHub Issues
                  <ExternalLink className="w-3 h-3" />
                </a>
              </p>
            </details>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;

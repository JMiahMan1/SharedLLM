import { Component, ErrorInfo, ReactNode, useState } from 'react';
import { 
  ShieldAlert, 
  RefreshCcw, 
  Home, 
  Copy, 
  ChevronDown, 
  ChevronRight, 
  Bug,
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
        typeof (error as Record<string, unknown>)[key] === 'string'
    );
    
    if (extraProps.length > 0) {
      const extraInfo = extraProps
        .map(key => `${key}: ${(error as Record<string, unknown>)[key]}`)
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

function ErrorSection({ detail, index }: { detail: ErrorDetail; index: number }) {
  const [isExpanded, setIsExpanded] = useState(index === 0);
  
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
        <div className="min-h-screen flex items-center justify-center bg-slate-950 p-4 font-sans">
          <div className="bg-slate-900 border border-red-500/30 rounded-xl p-8 max-w-3xl w-full text-center shadow-2xl">
            <ShieldAlert className="w-16 h-16 text-red-500 mx-auto mb-6" />
            <h1 className="text-2xl font-bold text-white mb-2">Jarvis Encountered a UI Error</h1>
            
            <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 mb-6">
              <div className="flex items-center gap-2 text-amber-400 text-sm">
                <Info className="w-4 h-4" />
                <span className="font-medium">{summary}</span>
              </div>
            </div>

            <p className="text-slate-400 mb-6">
              A component failed to render properly. The detailed error information below will help developers fix this issue.
            </p>
            
            {/* Error Details Panel */}
            <div className="text-left mb-6 space-y-2">
              {details.map((detail, index) => (
                <ErrorSection key={index} detail={detail} index={index} />
              ))}
            </div>

            {/* Debug Info */}
            <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 mb-6">
              <div className="flex items-center gap-2 text-blue-400 text-sm mb-2">
                <Bug className="w-4 h-4" />
                <span className="font-medium">For Developers</span>
              </div>
              <p className="text-slate-400 text-xs">
                Copy the error details above and file an issue at{' '}
                <a 
                  href="https://github.com/JMiahMan1/SharedLLM/issues" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="text-blue-400 hover:text-blue-300 underline flex items-center gap-1 inline-flex"
                >
                  GitHub Issues
                  <ExternalLink className="w-3 h-3" />
                </a>
              </p>
            </div>

            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <button 
                onClick={this.handleReset}
                className="flex items-center justify-center py-3 px-6 bg-red-600/10 hover:bg-red-600/20 text-red-500 border border-red-500/20 rounded-lg transition-colors font-medium flex-1"
              >
                <RefreshCcw className="w-5 h-5 mr-2" />
                Reload Page
              </button>
              
              <button 
                onClick={this.handleGoHome}
                className="flex items-center justify-center py-3 px-6 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium flex-1 shadow-lg shadow-blue-900/20"
              >
                <Home className="w-5 h-5 mr-2" />
                Return to Dashboard
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;

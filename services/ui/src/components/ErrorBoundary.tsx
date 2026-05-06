import { Component, ErrorInfo, ReactNode } from 'react';
import { ShieldAlert, RefreshCcw, Home } from 'lucide-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    // Update state so the next render will show the fallback UI.
    return { hasError: true, error, errorInfo: null };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught React UI error:', error, errorInfo);
    
    // Check if this is a Vite chunk loading error (often happens after a new deployment)
    // If it is, automatically hard-reload the page to fetch the new JavaScript chunks.
    if (error.message.includes('dynamically imported module') || error.message.includes('Failed to fetch dynamically imported module')) {
        window.location.reload();
    }

    this.setState({
      error,
      errorInfo
    });
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.reload();
  };

  private handleGoHome = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.href = '/dashboard';
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-slate-950 p-4 font-sans">
          <div className="bg-slate-900 border border-red-500/30 rounded-xl p-8 max-w-2xl w-full text-center shadow-2xl">
            <ShieldAlert className="w-16 h-16 text-red-500 mx-auto mb-6" />
            <h1 className="text-2xl font-bold text-white mb-2">Jarvis Encountered a UI Error</h1>
            <p className="text-slate-400 mb-6">
              A component failed to render properly. You can try reloading the interface or returning to the dashboard.
            </p>
            
            <div className="text-red-400/90 mb-8 font-mono text-sm overflow-x-auto whitespace-pre-wrap text-left bg-black/50 p-4 rounded border border-slate-800 max-h-48 overflow-y-auto">
              <strong>{this.state.error?.name}:</strong> {this.state.error?.message}
              {this.state.errorInfo && (
                <div className="mt-4 text-xs text-slate-500 opacity-70">
                  {this.state.errorInfo.componentStack}
                </div>
              )}
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

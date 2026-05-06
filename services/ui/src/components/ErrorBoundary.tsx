import { Component, ErrorInfo, ReactNode } from 'react';
import { ShieldAlert, RefreshCcw, Home } from 'lucide-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught error:', error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-[#050505] flex items-center justify-center p-6 font-sans">
          <div className="max-w-md w-full glass-panel p-10 text-center border-red-500/20 bg-red-500/5 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-red-500/50 to-transparent" />
            
            <div className="w-20 h-20 bg-red-500/10 rounded-3xl flex items-center justify-center mx-auto mb-8 animate-pulse">
              <ShieldAlert size={40} className="text-red-500" />
            </div>

            <h1 className="text-2xl font-black text-white tracking-tighter uppercase mb-4">Neural Link Severed</h1>
            <p className="text-slate-400 text-sm leading-relaxed mb-8">
              A critical exception occurred in the UI kernel. The matrix has been de-synchronized to prevent state corruption.
            </p>

            <div className="bg-black/40 rounded-xl p-4 mb-8 border border-white/5 text-left">
              <p className="text-[10px] uppercase font-black text-slate-500 tracking-widest mb-2">Error Signature</p>
              <code className="text-[11px] text-red-400 font-mono break-all">
                {this.state.error?.message || 'Unknown kernel panic'}
              </code>
            </div>

            <div className="flex flex-col gap-3">
              <button 
                onClick={() => window.location.reload()}
                className="w-full py-4 bg-red-600 hover:bg-red-500 text-white rounded-xl font-black uppercase tracking-widest text-xs transition-all flex items-center justify-center gap-3 group"
              >
                <RefreshCcw size={16} className="group-hover:rotate-180 transition-transform duration-500" />
                Initialize Hot-Reload
              </button>
              <button 
                onClick={() => window.location.href = '/'}
                className="w-full py-4 bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white rounded-xl font-black uppercase tracking-widest text-xs transition-all flex items-center justify-center gap-3"
              >
                <Home size={16} />
                Return to Nexus
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.children;
  }
}

export default ErrorBoundary;

import { useState, useCallback, useEffect, useRef } from 'react';
import { AlertCircle, WifiOff, Wifi, X, Server } from 'lucide-react';
import { Capacitor } from '@capacitor/core';
import { checkConnectivity } from '../../lib/connectivity';
import { storageGet, storageSet } from '../../lib/storage';

const DISMISSED_KEY = 'server_banner_dismissed';

const ServerConfigBanner = () => {
  const [status, setStatus] = useState<'checking' | 'disconnected' | 'connected' | 'no-config'>('checking');
  const [dismissed, setDismissed] = useState(false);
  const intervalRef = useRef<number | null>(null);
  const mountedRef = useRef(false);

  const check = useCallback(async () => {
    if (!mountedRef.current) return;
    const serverUrl = await storageGet('jarvis_server_url');

    if (!serverUrl) {
      setStatus('no-config');
      return;
    }

    let url = serverUrl.trim();
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'http://' + url;
    }
    url = url.replace(/\/+$/, '');

    const result = await checkConnectivity(url);
    if (mountedRef.current) {
      setStatus(result.ok ? 'connected' : 'disconnected');
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (!Capacitor.isNativePlatform()) {
      mountedRef.current = false;
      return;
    }
    intervalRef.current = window.setInterval(check, 30000);
    // Defer initial check to avoid setState-in-effect
    const id = setTimeout(() => { if (mountedRef.current) check(); }, 0);
    return () => {
      mountedRef.current = false;
      clearTimeout(id);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [check]);

  const handleDismiss = useCallback(() => {
    setDismissed(true);
    storageSet(DISMISSED_KEY, 'true').catch(() => {});
  }, []);

  if (dismissed || !Capacitor.isNativePlatform()) return null;

  if (status === 'checking') {
    return (
      <div className="px-4 py-2 bg-amber-500/10 border-b border-amber-500/20 flex items-center justify-center gap-2 text-xs text-amber-400">
        <div className="w-3 h-3 border-2 border-amber-400 border-t-transparent rounded-full animate-spin" />
        Checking server connection...
        <button onClick={handleDismiss} className="ml-2 text-amber-500 hover:text-amber-300">
          <X size={12} />
        </button>
      </div>
    );
  }

  if (status === 'connected') {
    return (
      <div className="px-4 py-1.5 bg-green-500/5 border-b border-green-500/10 flex items-center justify-center gap-2 text-[10px] text-green-500/70">
        <Wifi size={10} />
        Connected to server
        <button onClick={handleDismiss} className="ml-1 text-green-600 hover:text-green-400">
          <X size={10} />
        </button>
      </div>
    );
  }

  if (status === 'no-config') {
    return (
      <div className="px-4 py-3 bg-red-500/10 border-b border-red-500/20">
        <div className="flex items-start gap-3">
          <WifiOff size={16} className="text-red-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-red-400">Server not configured</p>
            <p className="text-[11px] text-red-400/70 mt-0.5">Server URL is not set. Please configure it on the login screen.</p>
          </div>
          <button onClick={handleDismiss} className="text-red-500 hover:text-red-300 shrink-0">
            <X size={14} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-3 bg-red-500/10 border-b border-red-500/20">
      <div className="flex items-start gap-3">
        <AlertCircle size={16} className="text-red-400 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-red-400">Server unreachable</p>
          <p className="text-[11px] text-red-400/70 mt-0.5">Cannot connect to Jarvis server. Check your network or server URL.</p>
        </div>
        <div className="flex gap-1.5 shrink-0">
          <button
            onClick={check}
            className="flex items-center gap-1 px-2 py-1 bg-red-500/20 border border-red-500/30 rounded text-[10px] text-red-300 hover:bg-red-500/30 transition-colors"
          >
            <Server size={10} /> Retry
          </button>
          <button onClick={handleDismiss} className="text-red-500 hover:text-red-300">
            <X size={14} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default ServerConfigBanner;

import { useState, useCallback, useEffect, useRef } from 'react';
import { AlertCircle, WifiOff, X, Server, Check } from 'lucide-react';
import { Capacitor } from '@capacitor/core';
import { RECHECK_AFTER_FAILURE_MS, checkConnectivity, normalizeServerUrl, showUnreachable } from '../../lib/connectivity';
import { deployUnderway } from '../../lib/deployStatus';
import { storageGet, storageSet } from '../../lib/storage';
import { DEFAULT_SERVER_URL, SERVER_URL_EXAMPLE } from '../../lib/serverUrl';

const SERVER_URL_HINT = DEFAULT_SERVER_URL || SERVER_URL_EXAMPLE;

const ServerConfigBanner = () => {
  const [status, setStatus] = useState<'checking' | 'disconnected' | 'connected' | 'no-config'>('checking');
  const [dismissed, setDismissed] = useState(false);
  const [serverUrl, setServerUrl] = useState('');
  const [serverInput, setServerInput] = useState('');
  const [saving, setSaving] = useState(false);
  const intervalRef = useRef<number | null>(null);
  const mountedRef = useRef(false);
  const failuresRef = useRef(0);
  const recheckRef = useRef<number | null>(null);

  /** One check; true when it failed without settling anything, so worth repeating soon. */
  const check = useCallback(async (): Promise<boolean> => {
    if (!mountedRef.current) return false;
    const storedUrl = await storageGet('jarvis_server_url');

    if (!storedUrl) {
      setStatus('no-config');
      return false;
    }

    const url = normalizeServerUrl(storedUrl);
    setServerUrl(url);

    const result = await checkConnectivity(url);
    if (!mountedRef.current) return false;
    if (result.ok) {
      failuresRef.current = 0;
      setStatus('connected');
      return false;
    }
    failuresRef.current += 1;
    const updating = await deployUnderway();
    if (!mountedRef.current) return false;
    if (showUnreachable(failuresRef.current, updating)) {
      setStatus('disconnected');
    } else {
      // Not yet: an update or a phone waking up. Hide the "checking" strip;
      // the caller looks again soon rather than in half a minute.
      setStatus((prev) => (prev === 'checking' ? 'connected' : prev));
      return true;
    }
    return false;
  }, []);

  const handleSave = useCallback(async () => {
    const raw = serverInput.trim();
    if (!raw) return;
    setSaving(true);
    await storageSet('jarvis_server_url', normalizeServerUrl(raw));
    setSaving(false);
    void check();
  }, [serverInput, check]);

  useEffect(() => {
    mountedRef.current = true;
    if (!Capacitor.isNativePlatform()) {
      mountedRef.current = false;
      return;
    }
    const run = async () => {
      if (await check() && mountedRef.current) {
        if (recheckRef.current) clearTimeout(recheckRef.current);
        recheckRef.current = window.setTimeout(() => { recheckRef.current = null; void run(); }, RECHECK_AFTER_FAILURE_MS);
      }
    };
    intervalRef.current = window.setInterval(() => void run(), 30000);
    // Defer initial check to avoid setState-in-effect
    const id = setTimeout(() => { if (mountedRef.current) void run(); }, 0);
    return () => {
      mountedRef.current = false;
      clearTimeout(id);
      if (recheckRef.current) clearTimeout(recheckRef.current);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [check]);

  // Hidden until the app is next opened; a lost connection is worth
  // re-announcing then.
  const handleDismiss = useCallback(() => setDismissed(true), []);

  // Nothing to say while connected: a permanent "Connected" strip above every
  // page only took space.
  if (dismissed || !Capacitor.isNativePlatform() || status === 'connected') return null;

  if (status === 'checking') {
    return (
      <div className="px-4 py-2 bg-amber-500/10 border-b border-amber-500/20 flex items-center justify-center gap-2 text-xs text-amber-400">
        <div className="w-3 h-3 border-2 border-amber-400 border-t-transparent rounded-full animate-spin" />
        Checking server connection...
        <button onClick={handleDismiss} aria-label="Dismiss" className="ml-1 p-2 -my-2 text-amber-500 hover:text-amber-300">
          <X size={14} />
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
            <p className="text-[11px] text-red-400/70 mt-0.5">Enter your server URL, e.g. {SERVER_URL_HINT}.</p>
            <div className="flex items-center gap-2 mt-2">
              <input
                value={serverInput}
                onChange={(e) => setServerInput(e.target.value)}
                aria-label="Server URL"
                placeholder={SERVER_URL_HINT}
                className="flex-1 min-w-0 px-2 min-h-11 rounded bg-black/30 border border-red-500/30 text-xs text-white placeholder-red-400/40 outline-none focus:border-red-400"
              />
              <button
                onClick={handleSave}
                disabled={saving || !serverInput.trim()}
                className="flex items-center gap-1 px-3 min-h-11 bg-red-500/30 border border-red-500/40 rounded text-[11px] text-red-200 hover:bg-red-500/40 transition-colors disabled:opacity-50"
              >
                <Check size={11} /> {saving ? 'Saving' : 'Save'}
              </button>
            </div>
          </div>
          <button onClick={handleDismiss} aria-label="Dismiss" className="p-2 -m-2 text-red-500 hover:text-red-300 shrink-0">
            <X size={16} />
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
          <p className="text-[11px] text-red-400/70 mt-0.5">Cannot connect to {serverUrl}. Check the URL or your network, then save.</p>
          <div className="flex items-center gap-2 mt-2">
            <input
              value={serverInput}
              onChange={(e) => setServerInput(e.target.value)}
              aria-label="Server URL"
              placeholder={serverUrl || SERVER_URL_HINT}
              className="flex-1 min-w-0 px-2 min-h-11 rounded bg-black/30 border border-red-500/30 text-xs text-white placeholder-red-400/40 outline-none focus:border-red-400"
            />
            <button
              onClick={handleSave}
              disabled={saving || !serverInput.trim()}
              className="flex items-center gap-1 px-3 min-h-11 bg-red-500/30 border border-red-500/40 rounded text-[11px] text-red-200 hover:bg-red-500/40 transition-colors disabled:opacity-50"
            >
              <Check size={11} /> {saving ? 'Saving' : 'Save'}
            </button>
          </div>
        </div>
        <div className="flex flex-col gap-1.5 shrink-0">
          <button
            onClick={check}
            className="flex items-center gap-1 px-3 min-h-11 bg-red-500/20 border border-red-500/30 rounded text-[10px] text-red-300 hover:bg-red-500/30 transition-colors"
          >
            <Server size={10} /> Retry
          </button>
          <button onClick={handleDismiss} aria-label="Dismiss" className="self-end p-2 -m-1 text-red-500 hover:text-red-300">
            <X size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default ServerConfigBanner;

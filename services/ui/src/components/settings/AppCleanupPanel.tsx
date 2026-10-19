import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw, ShieldCheck, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useAuth } from '../../context/AuthContext';
import { resetMobileNavPaths } from '../../lib/mobileNav';

async function clearBrowserCaches(): Promise<number> {
  if (typeof window === 'undefined' || !('caches' in window)) return 0;
  const keys = await window.caches.keys();
  await Promise.all(keys.map((key) => window.caches.delete(key)));
  return keys.length;
}

/**
 * Per-device housekeeping. Clears only data this browser can rebuild from the
 * server: React Query's in-memory cache and Cache Storage entries. Tokens,
 * saved preferences and anything on the server stay untouched.
 */
export default function AppCleanupPanel() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [clearing, setClearing] = useState(false);

  const clearAppCache = async () => {
    setClearing(true);
    queryClient.clear();
    try {
      const removed = await clearBrowserCaches();
      toast.success(
        removed > 0
          ? `App cache cleared (${removed} browser cache${removed === 1 ? '' : 's'})`
          : 'App cache cleared',
      );
    } catch {
      toast.error('Data cache cleared, but browser caches could not be removed');
    } finally {
      setClearing(false);
    }
  };

  const resetMobileMenu = () => {
    resetMobileNavPaths(user?.is_admin ?? false);
    toast.success('Mobile menu reset to the default pages');
  };

  return (
    <section
      className="glass-panel rounded-2xl p-4 space-y-3"
      aria-labelledby="app-cleanup-heading"
      data-testid="app-cleanup-panel"
    >
      <div className="px-1">
        <h2
          id="app-cleanup-heading"
          className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2"
        >
          <Trash2 size={16} className="text-amber-400" />
          App cleanup
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          Safe, per-device housekeeping for this browser or app install.
        </p>
      </div>

      <div className="p-3 rounded-xl bg-white/5 space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-white text-sm font-medium">Clear app cache</p>
            <p className="text-xs text-slate-400 mt-0.5">
              Drops cached pages and API responses so the app fetches fresh copies from your
              server. The next screen you open may load a little slower.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void clearAppCache()}
            disabled={clearing}
            className="glass-button shrink-0 px-3 py-1.5 pointer-coarse:min-h-11 rounded-xl text-xs font-semibold text-amber-300 hover:text-white border-amber-500/30 flex items-center gap-1.5 disabled:opacity-50"
          >
            {clearing ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
            {clearing ? 'Clearing…' : 'Clear app cache'}
          </button>
        </div>
        <div className="flex items-start gap-2 text-[11px] leading-relaxed text-slate-500">
          <ShieldCheck size={13} className="shrink-0 mt-0.5 text-emerald-400/80" />
          <p>
            This does not sign you out or remove your settings, notes, messages, photos or any
            server data. Downloads held in browser Cache Storage may need to load again.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-white/5">
        <div className="min-w-0">
          <p className="text-white text-sm font-medium">Reset mobile menu</p>
          <p className="text-xs text-slate-400 mt-0.5">
            Restore the default pages and order on this device's bottom bar.
          </p>
        </div>
        <button
          type="button"
          onClick={resetMobileMenu}
          className="glass-button shrink-0 px-3 py-1.5 pointer-coarse:min-h-11 rounded-xl text-xs font-semibold text-purple-300 hover:text-white border-purple-500/30 flex items-center gap-1.5"
        >
          <RotateCcw size={13} />
          Reset mobile menu
        </button>
      </div>
    </section>
  );
}

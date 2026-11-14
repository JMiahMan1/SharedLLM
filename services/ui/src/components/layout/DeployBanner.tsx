import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { Loader2, RefreshCw, X } from 'lucide-react';
import {
  POLL_IDLE_MS, POLL_UPDATING_MS, getDeployStatus, nextPhase, type DeployBannerPhase,
} from '../../lib/deployStatus';

/**
 * A thin line under the top bar while Jarvis is being updated, so a pause reads as an update
 * and not as something broken; after an update that changed the web app, a browser is offered
 * a reload. Says nothing the rest of the time.
 */
export default function DeployBanner() {
  const [phase, setPhase] = useState<DeployBannerPhase>('none');
  const status = useQuery({
    queryKey: ['deploy-status'],
    queryFn: getDeployStatus,
    retry: false,
    refetchInterval: phase === 'updating' ? POLL_UPDATING_MS : POLL_IDLE_MS,
    refetchOnWindowFocus: true,
  });

  // Each answer moves the banner on once, judged at the time it arrived.
  const [seenAt, setSeenAt] = useState(0);
  if (status.isFetched && status.dataUpdatedAt !== seenAt) {
    setSeenAt(status.dataUpdatedAt);
    setPhase(nextPhase(phase, status.data ?? null, status.dataUpdatedAt, Capacitor.isNativePlatform()));
  }

  if (phase === 'none') return null;
  if (phase === 'updating') {
    return (
      <div role="status" data-testid="deploy-banner"
        className="flex min-w-0 items-center justify-center gap-2 border-b border-sky-400/20 bg-sky-500/10 px-4 py-1.5 text-xs text-sky-100">
        <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden />
        <span className="min-w-0">Jarvis is updating. Things may pause for a minute.</span>
      </div>
    );
  }
  return (
    <div role="status" data-testid="deploy-banner"
      className="flex min-w-0 flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-emerald-400/20 bg-emerald-500/10 px-4 py-1 text-xs text-emerald-100">
      <span className="min-w-0">Jarvis was updated.</span>
      <span className="flex items-center gap-1">
        <button type="button" onClick={() => window.location.reload()}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 font-semibold text-emerald-50 hover:bg-white/10">
          <RefreshCw size={13} aria-hidden /> Reload
        </button>
        <button type="button" onClick={() => setPhase('none')} aria-label="Dismiss"
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-emerald-200 hover:bg-white/10">
          <X size={14} aria-hidden />
        </button>
      </span>
    </div>
  );
}

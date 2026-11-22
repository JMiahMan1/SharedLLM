import { useQuery } from '@tanstack/react-query';
import { CloudOff } from 'lucide-react';
import { POLL_DOWN_MS, POLL_UP_MS, bannerText, getLocalAiStatus } from '../../lib/localAiStatus';

/**
 * A thin line under the top bar while Jarvis's local AI (or the games and music
 * on the same computer) is offline, saying what still works. Nothing the rest
 * of the time. It reads the gateway's status, which comes back on its own when
 * the computer does, so there is nothing to dismiss or retry here.
 */
export default function LocalAIBanner() {
  const status = useQuery({
    queryKey: ['local-ai-status'],
    queryFn: getLocalAiStatus,
    retry: false,
    refetchInterval: (query) => (query.state.data?.offline ? POLL_DOWN_MS : POLL_UP_MS),
    refetchOnWindowFocus: true,
  });
  const text = bannerText(status.data ?? null);
  if (!text) return null;
  return (
    <div role="status" data-testid="local-ai-banner"
      className="flex min-w-0 items-center justify-center gap-2 border-b border-amber-400/20 bg-amber-500/10 px-4 py-1.5 text-xs text-amber-100">
      <CloudOff size={13} className="shrink-0" aria-hidden />
      <span className="min-w-0 break-words">{text}</span>
    </div>
  );
}

import { Link } from 'react-router-dom';
import { CloudOff } from 'lucide-react';
import { useOfflineMode } from '../../hooks/useOfflineMode';

/**
 * A small line under the header while the Android app cannot reach the
 * server: the family chat and Voice Assistant questions go through nearby
 * family phones. It links to Settings > Nearby & offline > Offline mode. It goes away by itself when the server answers again.
 */
export default function OfflineModeBanner() {
  const { offline } = useOfflineMode();
  if (!offline) return null;
  return (
    <Link
      to="/settings#offline"
      data-testid="offline-mode-banner"
      className="flex min-h-11 items-center justify-center gap-2 bg-sky-900/60 px-4 text-xs font-semibold text-sky-100"
    >
      <CloudOff size={14} aria-hidden /> Offline, using nearby family phones
    </Link>
  );
}

import { useQuery } from '@tanstack/react-query';
import { Car, Navigation } from 'lucide-react';
import { api } from '../../services/api';

/** A maps link that opens the phone's maps app with driving directions. */
function directionsUrl(lat: number, lon: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat.toFixed(6)},${lon.toFixed(6)}&travelmode=driving`;
}

function minutes(seconds: number): string {
  const mins = Math.max(1, Math.round(seconds / 60));
  return mins < 60 ? `${mins} min` : `${Math.floor(mins / 60)} h ${mins % 60} min`;
}

/**
 * Navigate to a family member, and how long the drive is from where you are
 * (by road, from your own latest position; nothing shown when that is not
 * known). Clicks stay here: the card around it centres the map.
 */
export default function MemberDirections({ me, name, lat, lon }: { me: string; name: string; lat: number; lon: number }) {
  // Rounded so a member creeping a few metres does not ask for a new drive time.
  const to = `${lat.toFixed(3)},${lon.toFixed(3)}`;
  const eta = useQuery({
    queryKey: ['drive-time', me, to],
    queryFn: () => api.getEta(me, to),
    enabled: Boolean(me),
    retry: false,
    staleTime: 5 * 60_000,
  });
  return (
    <div className="flex flex-wrap items-center justify-between gap-2" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <span className="flex items-center gap-1.5 text-[11px] text-slate-400" data-testid="member-drive-time">
        {eta.data && !eta.data.arrived ? (
          <>
            <Car size={13} className="text-sky-300" /> {minutes(eta.data.duration_s)} away by car
          </>
        ) : eta.data?.arrived ? (
          'Right where you are'
        ) : null}
      </span>
      <a
        href={directionsUrl(lat, lon)}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Directions to ${name}`}
        className="glass-button flex min-h-11 items-center gap-1.5 rounded-xl px-3 text-xs font-semibold text-sky-200"
      >
        <Navigation size={13} /> Navigate
      </a>
    </div>
  );
}

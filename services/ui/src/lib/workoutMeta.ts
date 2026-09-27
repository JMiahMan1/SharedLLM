import { Footprints, PersonStanding, Bike, Mountain, Zap, PawPrint, Car } from 'lucide-react';

export type ActivityType =
  | 'driving'
  | 'walking'
  | 'running'
  | 'cycling'
  | 'mountain_biking'
  | 'dirtbiking'
  | 'horseback_riding';

export const WORKOUT_OPTIONS: Array<{ type: ActivityType; label: string; icon: typeof Footprints; color: string }> = [
  { type: 'walking', label: 'Walk', icon: Footprints, color: 'text-emerald-400' },
  { type: 'running', label: 'Run', icon: PersonStanding, color: 'text-rose-400' },
  { type: 'cycling', label: 'Bike Ride', icon: Bike, color: 'text-sky-400' },
  { type: 'mountain_biking', label: 'Mountain Bike', icon: Mountain, color: 'text-orange-400' },
  { type: 'dirtbiking', label: 'Dirtbike Ride', icon: Zap, color: 'text-amber-400' },
  { type: 'horseback_riding', label: 'Horseback Ride', icon: PawPrint, color: 'text-purple-400' },
];

export const ACTIVITY_LABELS: Record<string, string> = {
  driving: 'Drive',
  walking: 'Walk',
  running: 'Run',
  cycling: 'Bike Ride',
  mountain_biking: 'Mountain Bike',
  dirtbiking: 'Dirtbike Ride',
  horseback_riding: 'Horseback Ride',
};

export const ACTIVITY_ICONS: Record<string, typeof Footprints> = {
  driving: Car,
  walking: Footprints,
  running: PersonStanding,
  cycling: Bike,
  mountain_biking: Mountain,
  dirtbiking: Zap,
  horseback_riding: PawPrint,
};

/** "45m" / "2h 05m" label for a duration in seconds. */
export function formatDuration(seconds: number): string {
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hrs}h ${remMins}m`;
}

/** "Mar 3, 9:15 AM – 10:02 AM" style range from unix seconds. */
export function formatTimeRange(start: number, end: number): string {
  const sDate = new Date(start * 1000);
  const eDate = new Date(end * 1000);
  const sameDay = sDate.toDateString() === eDate.toDateString();

  const timeStr = sDate.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const endTimeStr = eDate.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const dateStr = sDate.toLocaleDateString([], { month: 'short', day: 'numeric' });

  if (sameDay) {
    return `${dateStr}, ${timeStr} – ${endTimeStr}`;
  }
  return `${dateStr} ${timeStr} – ${eDate.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${endTimeStr}`;
}

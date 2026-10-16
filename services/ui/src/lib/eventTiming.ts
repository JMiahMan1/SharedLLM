const DAY_MS = 86_400_000;

/** "Now", "In 20 min", "In 3 hours", "Tomorrow", "Fri", "Oct 30". Days are
 * counted by calendar date, so an event at 9am tomorrow is "Tomorrow" even
 * when it is less than 24 hours away. */
export const formatRelativeTime = (date: Date, end?: Date, now = new Date()): string => {
  const diffMs = date.getTime() - now.getTime();
  if (diffMs <= 0) return end && end.getTime() > now.getTime() ? 'Now' : 'Started';
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dayDiff = Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);
  if (dayDiff === 0) {
    const mins = Math.round(diffMs / 60_000);
    if (mins < 60) return `In ${Math.max(1, mins)} min`;
    const hours = Math.round(mins / 60);
    return `In ${hours} hour${hours === 1 ? '' : 's'}`;
  }
  if (dayDiff === 1) return 'Tomorrow';
  if (dayDiff < 7) return date.toLocaleDateString([], { weekday: 'short' });
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
};

/** Still worth listing: not yet over (or, with no end time, started within the hour). */
export const isUpcoming = (event: { start_time: string; end_time?: string }, nowMs = Date.now()): boolean => {
  const end = event.end_time ? new Date(event.end_time).getTime() : NaN;
  if (!Number.isNaN(end)) return end > nowMs;
  return new Date(event.start_time).getTime() > nowMs - 3_600_000;
};

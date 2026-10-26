/**
 * The notice categories people can switch on and off (docs/PUSH.md). The
 * Android app names its notification channels the same way
 * (PushCategories.java), so a category reads alike in both settings screens.
 */
export interface NotificationCategory {
  id: string;
  label: string;
  description: string;
  /** Shown to admins only. */
  adminOnly?: boolean;
  /** Part of the category cannot be turned off; said under the switch. */
  note?: string;
}

export const NOTIFICATION_CATEGORIES: NotificationCategory[] = [
  { id: 'chat', label: 'Family chat', description: 'New messages and Jarvis answers in your conversations.' },
  { id: 'call', label: 'Family calls', description: 'When somebody starts a family call.' },
  { id: 'family_presence', label: 'Arrivals and departures', description: 'When family arrive at or leave a saved place.' },
  {
    id: 'family_safety',
    label: 'Family safety',
    description: 'Low phone battery, school arrivals, long stops somewhere unknown, and On my way.',
    note: 'SOS alerts always come through, even with this off.',
  },
  { id: 'health', label: 'Health and fitness', description: 'Step goals reached and your weekly summary.' },
  { id: 'leave_by', label: 'Time to leave', description: 'When to head out to reach your next event on time.' },
  { id: 'calendar', label: 'Calendar reminders', description: 'Before events on your calendars.' },
  { id: 'timers', label: 'Timers and alarms', description: 'When a timer or alarm goes off.' },
  { id: 'bible', label: 'Bible reading', description: 'Reminders for your reading plan.' },
  { id: 'home', label: 'Home', description: 'Doorbell, motion while away, doors, the garage, laundry and leaks.' },
  { id: 'reports', label: 'Reports', description: 'When a scheduled report is ready.' },
  { id: 'jobs', label: 'Finished jobs', description: 'Songs, pictures and audiobooks that are ready.' },
  { id: 'raven', label: 'Raven missions', description: 'Missions that finished or need your approval.' },
  { id: 'app_updates', label: 'App updates', description: 'A new version of the Jarvis app is ready to install.' },
  {
    id: 'admin',
    label: 'System alerts',
    description: 'Services down or back, error spikes, deploys, backups and disk space.',
    adminOnly: true,
  },
];

/** The categories this person can change. */
export function categoriesFor(isAdmin: boolean): NotificationCategory[] {
  return NOTIFICATION_CATEGORIES.filter((c) => isAdmin || !c.adminOnly);
}

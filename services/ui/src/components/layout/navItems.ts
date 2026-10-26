import {
  BookOpen,
  Brain,
  Calendar,
  Compass,
  Database,
  FileText,
  FolderKanban,
  HardDrive,
  HeartPulse,
  LayoutDashboard,
  MonitorSmartphone,
  Music,
  Shield,
  ShieldHalf,
  SlidersHorizontal,
  Smartphone,
  StickyNote,
  Users,
  type LucideIcon,
} from 'lucide-react';

/**
 * The app's pages, in one list for every navigator: the desktop sidebar, the
 * phone bottom bar (+ its More sheet) and the header's page finder. Three
 * hand-kept copies drifted: Lab was offered to non-admins on desktop (the
 * route bounces them), and Knowledge, Workspaces and Docs could not be
 * reached from a phone at all.
 */
export interface NavPage {
  icon: LucideIcon;
  label: string;
  path: string;
  /** Extra words the header finder matches on. */
  keywords: string;
  adminOnly?: boolean;
  /** Label in the phone bottom bar, when it differs ("Home"). */
  mobileLabel?: string;
  /**
   * Label for a signed-in member who is not an admin, when the page means
   * something different to them. "Devices" is device management for a parent
   * and a list of the files a parent shares for a child, so the same word sent
   * a child somewhere they had no reason to look.
   */
  memberLabel?: string;
  /** Default slot (1-5) among the tabs pinned to the phone bottom bar. */
  pinnedOnMobile?: number;
}

export const NAV_PAGES: NavPage[] = [
  { icon: LayoutDashboard, label: 'Dashboard', mobileLabel: 'Home', path: '/', keywords: 'home overview jarvis widgets', pinnedOnMobile: 1 },
  { icon: Compass, label: 'Wander', path: '/wander', keywords: 'trips family circle presence travel drives vehicles fuel mileage locations map', pinnedOnMobile: 2 },
  { icon: HeartPulse, label: 'Health', path: '/fitness', keywords: 'steps workouts pedometer fitness goals achievements', pinnedOnMobile: 5 },
  { icon: Users, label: 'Family', path: '/family', keywords: 'games talk chores rewards together recipes timers alarms announcements speakers devices notifications', pinnedOnMobile: 3 },
  { icon: ShieldHalf, label: 'Guardian', path: '/guardian', keywords: 'family guardian parental controls screen time safety children kids wellbeing' },
  { icon: Smartphone, label: 'Devices', memberLabel: 'Shared files', path: '/devices', keywords: 'mdm device management inventory remote wipe lock update push shared school library files' },
  { icon: MonitorSmartphone, label: 'Remote', path: '/remote', keywords: 'remote control tv devices home assistant' },
  { icon: BookOpen, label: 'Bible', path: '/bible', keywords: 'scripture read verse devotional plan memorise quiz study blb' },
  { icon: StickyNote, label: 'Notes', path: '/notes', keywords: 'notes lists' },
  { icon: Calendar, label: 'Calendar', path: '/calendar', keywords: 'schedule events' },
  { icon: Music, label: 'Media', path: '/media', keywords: 'music assistant audiobookshelf player', pinnedOnMobile: 4 },
  { icon: FolderKanban, label: 'Workspaces', path: '/workspaces', keywords: 'files folders registry' },
  { icon: HardDrive, label: 'Artifacts', path: '/artifacts', keywords: 'storage files generated audio images video documents quota' },
  { icon: Database, label: 'Knowledge', path: '/knowledge', keywords: 'rag semantic memory storage indexed files ha entities' },
  { icon: Brain, label: 'Lab', path: '/lab', keywords: 'raven missions autonomous agent', adminOnly: true },
  { icon: FileText, label: 'Docs', path: '/docs', keywords: 'documentation help api reference' },
  { icon: SlidersHorizontal, label: 'Settings', path: '/settings', keywords: 'preferences configuration profile' },
  { icon: Shield, label: 'Admin', path: '/admin', keywords: 'manage control panel users services', adminOnly: true },
];

/**
 * The pages this user may open, in display order, with the labels that fit
 * them: a member who is not an admin sees "Shared files" where an admin sees
 * "Devices".
 */
export function navPagesFor(isAdmin: boolean): NavPage[] {
  return NAV_PAGES.filter((page) => !page.adminOnly || isAdmin).map((page) =>
    !isAdmin && page.memberLabel ? { ...page, label: page.memberLabel } : page
  );
}

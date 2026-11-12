import {
  BookOpen,
  Brain,
  Calendar,
  Cctv,
  ChefHat,
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
import { History as HistoryIcon, House, Thermometer, Zap } from 'lucide-react';
import { CalendarClock, Workflow } from 'lucide-react';

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
  /** The section this page is listed under in the sidebar and the More sheet. */
  group: NavGroupKey;
  /** The section for a member who is not an admin, when it differs. */
  memberGroup?: NavGroupKey;
}

/**
 * Sections of the nav. Seven home pages (Rooms, Climate, Energy, History,
 * Automations, Schedules, Cameras) landed at once and made one flat list of
 * twenty-six links; grouping them keeps "turn on a light" next to the other
 * everyday home controls and "write an automation" with the setup pages.
 */
export type NavGroupKey = 'everyday' | 'home' | 'manage' | 'library' | 'system';

export interface NavGroup {
  key: NavGroupKey;
  /** Heading shown above the section; the first section has none. */
  label: string;
}

export const NAV_GROUPS: NavGroup[] = [
  { key: 'everyday', label: 'Everyday' },
  { key: 'home', label: 'Home' },
  { key: 'manage', label: 'Manage home' },
  { key: 'library', label: 'Files and knowledge' },
  { key: 'system', label: 'System' },
];

export const NAV_PAGES: NavPage[] = [
  { icon: LayoutDashboard, label: 'Dashboard', mobileLabel: 'Home', path: '/', keywords: 'home overview jarvis widgets', pinnedOnMobile: 1, group: 'everyday' },
  { icon: Compass, label: 'Wander', path: '/wander', keywords: 'trips family circle presence travel drives vehicles fuel mileage locations map', pinnedOnMobile: 2, group: 'everyday' },
  { icon: HeartPulse, label: 'Health', path: '/fitness', keywords: 'steps workouts pedometer fitness goals achievements', pinnedOnMobile: 5, group: 'everyday' },
  { icon: Users, label: 'Family', path: '/family', keywords: 'games talk chores rewards together recipes timers alarms announcements speakers devices notifications', pinnedOnMobile: 3, group: 'everyday' },
  { icon: ShieldHalf, label: 'Guardian', path: '/guardian', keywords: 'family guardian parental controls screen time safety children kids wellbeing', group: 'everyday' },
  { icon: House, label: 'Rooms', path: '/rooms', keywords: 'rooms areas lights switches thermostats media sensors home control', group: 'home' },
  { icon: Thermometer, label: 'Climate', path: '/climate', keywords: 'thermostat temperature humidity heating cooling ac air handler', group: 'home' },
  { icon: Cctv, label: 'Cameras', path: '/cameras', keywords: 'cameras live view security footage recordings garage backyard reolink doorbell', group: 'home' },
  { icon: Zap, label: 'Energy', path: '/energy', keywords: 'energy power electricity usage kwh cost circuits peak utility bill', group: 'home' },
  { icon: MonitorSmartphone, label: 'Remote', path: '/remote', keywords: 'remote control tv devices home assistant', group: 'home' },
  { icon: CalendarClock, label: 'Schedules', path: '/schedules', keywords: 'schedules thermostat programs hold away vacation energy timers scheduler', group: 'manage' },
  { icon: Workflow, label: 'Automations', path: '/automations', keywords: 'automations scripts scenes rules triggers routines traces', group: 'manage' },
  { icon: Smartphone, label: 'Devices', memberLabel: 'Shared files', path: '/devices', keywords: 'mdm device management inventory remote wipe lock update push shared school library files integrations pairing matter esphome add new phones', group: 'manage', memberGroup: 'library' },
  { icon: HistoryIcon, label: 'History', path: '/history', keywords: 'history graph chart sensor states timeline statistics', group: 'manage' },
  { icon: BookOpen, label: 'Bible', path: '/bible', keywords: 'scripture read verse devotional plan memorise quiz study blb', group: 'everyday' },
  { icon: StickyNote, label: 'Notes', path: '/notes', keywords: 'notes lists', group: 'everyday' },
  // The recipes page had a route but no entry anywhere, so the only way to it
  // was Family -> Create, which mounts the same screen inline.
  { icon: ChefHat, label: 'Recipes', path: '/recipes', keywords: 'recipes meals cooking food kitchen meal plan groceries ingredients', group: 'everyday' },
  { icon: Calendar, label: 'Calendar', path: '/calendar', keywords: 'schedule events', group: 'everyday' },
  { icon: Music, label: 'Media', path: '/media', keywords: 'music assistant audiobookshelf player', pinnedOnMobile: 4, group: 'everyday' },
  { icon: FolderKanban, label: 'Workspaces', path: '/workspaces', keywords: 'files folders registry', group: 'library' },
  { icon: HardDrive, label: 'Artifacts', path: '/artifacts', keywords: 'storage files generated audio images video documents quota', group: 'library' },
  { icon: Database, label: 'Knowledge', path: '/knowledge', keywords: 'rag semantic memory storage indexed files ha entities', group: 'library' },
  { icon: Brain, label: 'Lab', path: '/lab', keywords: 'raven missions autonomous agent', adminOnly: true, group: 'system' },
  { icon: FileText, label: 'Docs', path: '/docs', keywords: 'documentation help api reference', group: 'library' },
  { icon: SlidersHorizontal, label: 'Settings', path: '/settings', keywords: 'preferences configuration profile', group: 'system' },
  { icon: Shield, label: 'Admin', path: '/admin', keywords: 'manage control panel users services', adminOnly: true, group: 'system' },
];

/**
 * The pages this user may open, in display order, with the labels that fit
 * them: a member who is not an admin sees "Shared files" where an admin sees
 * "Devices".
 */
export function navPagesFor(isAdmin: boolean): NavPage[] {
  return NAV_PAGES.filter((page) => !page.adminOnly || isAdmin).map((page) => {
    if (isAdmin) return page;
    return {
      ...page,
      ...(page.memberLabel ? { label: page.memberLabel } : {}),
      ...(page.memberGroup ? { group: page.memberGroup } : {}),
    };
  });
}

export interface NavSection extends NavGroup {
  pages: NavPage[];
}

/**
 * The pages this user may open, in sections, in `NAV_GROUPS` order. A section
 * with nothing in it for this role is left out, so a member never sees an
 * empty "System" heading.
 */
export function navSectionsFor(isAdmin: boolean, pages: NavPage[] = navPagesFor(isAdmin)): NavSection[] {
  return NAV_GROUPS.map((group) => ({ ...group, pages: pages.filter((page) => page.group === group.key) }))
    .filter((section) => section.pages.length > 0);
}

/** The section a path belongs to (its own page or a child route), if any. */
export function sectionOfPath(sections: NavSection[], pathname: string): NavGroupKey | null {
  for (const section of sections) {
    for (const page of section.pages) {
      if (page.path === '/' ? pathname === '/' : pathname === page.path || pathname.startsWith(`${page.path}/`)) {
        return section.key;
      }
    }
  }
  return null;
}

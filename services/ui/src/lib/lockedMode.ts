import { useEffect, useSyncExternalStore } from 'react';
import { Capacitor } from '@capacitor/core';
import { BookOpen, Calendar, Hourglass, Lightbulb, ListChecks, MessageCircle, Music, Phone, type LucideIcon } from 'lucide-react';
import Guard, { type LockedModeState } from '../plugins/guard';

/**
 * Locked mode: Jarvis opened from the screen-time lock ("Open Jarvis" on the
 * guard's lock screen). The phone stays locked; Jarvis shows over it with only
 * the sections a parent left open.
 *
 * Which sections are open comes from the server (the child's screen-time
 * rules, ``locked_sections``, edited on the Guardian page) by way of the
 * phone's guard, which already syncs those rules and tells the web app
 * (JarvisGuard's ``lockedMode``). This file only knows what each section id
 * means in this app: its label, icon and the pages it opens.
 */

export type LockedSectionId = 'chat' | 'calls' | 'devices' | 'time' | 'chores' | 'bible' | 'calendar' | 'music';

/** A page a section opens: a path and its sub-paths, or one tab of a page. */
interface LockedRoute {
  path: string;
  /** Only this ``?tab=`` of the page (the Family page's chat or calls). */
  tab?: string;
  /** The tab shown when the address names none. */
  defaultTab?: string;
}

export interface LockedSection {
  id: LockedSectionId;
  label: string;
  icon: LucideIcon;
  /** Where its nav button goes; none for a section shown on the locked home only. */
  to?: string;
  routes: LockedRoute[];
}

/** The Family page's tab when the address names none (Family.tsx). */
const FAMILY_DEFAULT_TAB = 'chat';

export const LOCKED_SECTIONS: LockedSection[] = [
  { id: 'chat', label: 'Chat', icon: MessageCircle, to: '/family',
    routes: [{ path: '/family', tab: 'chat', defaultTab: FAMILY_DEFAULT_TAB }] },
  { id: 'calls', label: 'Calls', icon: Phone, to: '/family?tab=calls',
    routes: [{ path: '/family', tab: 'calls', defaultTab: FAMILY_DEFAULT_TAB }] },
  // Lights and the rest are the DeviceControlWidget on the locked home, under
  // the child's own entity permissions (the gateway enforces them), plus the TV remote.
  { id: 'devices', label: 'Devices', icon: Lightbulb, to: '/remote', routes: [{ path: '/remote' }] },
  // Time left and asking for more, and chores, are cards on the locked home.
  { id: 'time', label: 'My time', icon: Hourglass, routes: [] },
  { id: 'chores', label: 'Chores', icon: ListChecks, routes: [] },
  { id: 'bible', label: 'Bible', icon: BookOpen, to: '/bible', routes: [{ path: '/bible' }] },
  { id: 'calendar', label: 'Calendar', icon: Calendar, to: '/calendar', routes: [{ path: '/calendar' }] },
  { id: 'music', label: 'Music', icon: Music, to: '/media', routes: [{ path: '/media' }] },
];

const KNOWN = new Map(LOCKED_SECTIONS.map((s) => [s.id, s]));
/** What the lock's own button opens, so it is open whatever the list says. */
const ALWAYS_OPEN: LockedSectionId = 'chat';

/**
 * The server's list as this app can use it: known ids, each once, in the
 * server's order, with chat first. An empty list (an older server, or none
 * synced yet) leaves chat alone.
 */
export function normalizeSections(ids: readonly string[] | null | undefined): LockedSectionId[] {
  const out: LockedSectionId[] = [ALWAYS_OPEN];
  for (const id of ids || []) {
    if (KNOWN.has(id as LockedSectionId) && !out.includes(id as LockedSectionId)) out.push(id as LockedSectionId);
  }
  return out;
}

/** The sections for the locked mode's own navigation (those that open a page), in order. */
export function lockedNav(sections: readonly LockedSectionId[]): LockedSection[] {
  return sections.flatMap((id) => {
    const section = KNOWN.get(id);
    return section?.to ? [section] : [];
  });
}

const under = (pathname: string, path: string) => pathname === path || pathname.startsWith(`${path}/`);

/**
 * Whether a page may open in locked mode. The locked home (``/``) always may;
 * every other page only when one of the open sections lists it. Settings,
 * admin, identity and every page not listed stay locked.
 */
export function lockedPathAllowed(pathname: string, search: string, sections: readonly LockedSectionId[]): boolean {
  if (pathname === '/' || pathname === '') return true;
  const tab = new URLSearchParams(search).get('tab');
  return sections.some((id) =>
    (KNOWN.get(id)?.routes || []).some((route) => {
      if (route.tab !== undefined) return pathname === route.path && (tab || route.defaultTab) === route.tab;
      return under(pathname, route.path);
    }),
  );
}

/** The Family page tabs open in locked mode (chat, calls). */
export function lockedFamilyTabs(sections: readonly LockedSectionId[]): Set<string> {
  const tabs = new Set<string>();
  for (const id of sections) {
    for (const route of KNOWN.get(id)?.routes || []) {
      if (route.path === '/family' && route.tab) tabs.add(route.tab);
    }
  }
  return tabs;
}

/** Whether a link leaves the app: another origin, another scheme, or a new window. */
export function leavesApp(href: string | null, target: string | null, origin: string): boolean {
  if (target && target !== '_self') return true;
  if (!href) return false;
  if (href.startsWith('#')) return false;
  try {
    return new URL(href, origin).origin !== origin;
  } catch {
    return true;
  }
}

/**
 * While locked, no link may leave Jarvis: another site, a tel:/mailto:/intent:
 * link or a new window would hand the child to another app. Returns a function
 * that puts things back.
 */
export function blockExternalNavigation(doc: Document = document, win: Window = window): () => void {
  const onClick = (event: MouseEvent) => {
    const el = event.target instanceof Element ? event.target.closest('a') : null;
    if (!el) return;
    if (leavesApp(el.getAttribute('href'), el.getAttribute('target'), win.location.origin)) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  doc.addEventListener('click', onClick, true);
  const open = win.open;
  win.open = (() => null) as typeof win.open;
  return () => {
    doc.removeEventListener('click', onClick, true);
    win.open = open;
  };
}

export interface LockedMode {
  active: boolean;
  sections: LockedSectionId[];
}

const OFF: LockedMode = { active: false, sections: normalizeSections([]) };
let current: LockedMode = OFF;
const subscribers = new Set<() => void>();
let started = false;

/** Apply what native said (exported for tests and for the plugin listener). */
export function setLockedMode(next: Partial<LockedModeState> | null | undefined): void {
  const active = Boolean(next?.active);
  const sections = normalizeSections(next?.sections);
  if (active === current.active && sections.join() === current.sections.join()) return;
  current = { active, sections };
  subscribers.forEach((notify) => notify());
}

/** Ask native once for the state and listen for changes (Android only; elsewhere it is never on). */
export function startLockedMode(): void {
  if (started) return;
  started = true;
  if (Capacitor.getPlatform() !== 'android') return;
  void Guard.lockedMode().then(setLockedMode).catch(() => {
    // An older APK without locked mode: Jarvis is never opened over the lock there.
  });
  void Guard.addListener('lockedMode', setLockedMode).catch(() => undefined);
}

// The hermetic browser tests run on the dev server, where there is no native
// Guard to ask; they turn locked mode on through this hook. A production
// build drops it (import.meta.env.DEV is false there).
if (import.meta.env.DEV && typeof window !== 'undefined') {
  (window as unknown as { __jarvisSetLockedMode?: typeof setLockedMode }).__jarvisSetLockedMode = setLockedMode;
}

/** For tests: forget the state and start over. */
export function resetLockedModeForTests(): void {
  current = OFF;
  started = false;
  subscribers.clear();
}

const subscribe = (notify: () => void) => {
  subscribers.add(notify);
  return () => {
    subscribers.delete(notify);
  };
};
const snapshot = () => current;

/** Locked mode now; re-renders when it changes. */
export function useLockedMode(): LockedMode {
  useEffect(() => {
    startLockedMode();
  }, []);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

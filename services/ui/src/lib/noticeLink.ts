/**
 * Where a tapped notice goes (docs/PUSH.md, "Deep links").
 *
 * Every notice carries `data.link`, built on the server by one helper
 * (services/common/notice_links.py): a route of `noticeRoutes.json`, its
 * page, the query the page reads, a section anchor, and `focus`, the one item
 * on the page the notice is about (`msg-42`, `mission-7`, `unlock-21`). Older
 * notices only have `data.url`, an in-app address, which is followed as before.
 *
 * The bell, browser notifications, toasts and the Android app all go through
 * `noticeHref`, and then through the router like any other navigation: a link
 * never skips a route's guard (admin pages, a Beloved chat, a locked private
 * chat). The page marks its items with `data-focus-id`, and
 * components/layout/DeepLinkFocus.tsx scrolls to and highlights the one named,
 * or says it is gone.
 */
import table from './noticeRoutes.json';

export const FOCUS_PARAM: string = table.focus_param;

export interface RouteSpec {
  path: string;
  query: string[];
  hashes: string[];
  hash_pattern?: string;
  focus: string[];
  guard: string;
  query_guards: Record<string, string>;
  choices: Record<string, string[]>;
  description: string;
}

export const NOTICE_ROUTES: Record<string, RouteSpec> = table.routes as Record<string, RouteSpec>;

export interface NoticeLink {
  route: string;
  path: string;
  query?: Record<string, string>;
  hash?: string;
  focus?: string;
  guard?: string;
  href?: string;
}

const FOCUS_RE = /^([a-z][a-z_]{0,31})-([A-Za-z0-9_.:@~-]{1,160})$/;

/** A notice's link when it is a path inside the app; anything else is not followed. */
export function inAppPath(url: unknown): string | null {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') && !url.startsWith('/\\') ? url : null;
}

/** The placeholders of `spec.path` filled from `path`, or null when it is another page. */
export function matchRoute(spec: RouteSpec, path: string): Record<string, string> | null {
  const want = spec.path.split('/');
  const have = path.split('/');
  if (want.length !== have.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < want.length; i += 1) {
    const m = /^\{([a-z_]+)\}$/.exec(want[i]);
    if (m) {
      if (!have[i]) return null;
      let value: string;
      try {
        value = decodeURIComponent(have[i]);
      } catch {
        return null;
      }
      const allowed = spec.choices[m[1]];
      if (allowed && !allowed.includes(value)) return null;
      out[m[1]] = value;
    } else if (want[i] !== have[i]) {
      return null;
    }
  }
  return out;
}

/**
 * The in-app address of a structured link, or null when no page of this app
 * reads it (an unknown route, a parameter the page ignores, a newer server's
 * route this build does not have yet). Only what the route allows is kept.
 */
export function linkHref(raw: unknown): string | null {
  if (!raw || typeof raw !== 'object') return null;
  const link = raw as NoticeLink;
  const spec = NOTICE_ROUTES[String(link.route ?? '')];
  if (!spec) return null;
  const path = inAppPath(link.path);
  if (!path || !matchRoute(spec, path.split(/[?#]/)[0])) return null;
  const params = new URLSearchParams();
  const query = link.query && typeof link.query === 'object' ? link.query : {};
  for (const [key, value] of Object.entries(query)) {
    if (!spec.query.includes(key) || typeof value !== 'string') return null;
    const allowed = spec.choices[key];
    if (allowed && !allowed.includes(value)) return null;
    params.set(key, value);
  }
  const focus = typeof link.focus === 'string' ? link.focus : '';
  if (focus) {
    const m = FOCUS_RE.exec(focus);
    if (!m || !spec.focus.includes(m[1])) return null;
    params.set(FOCUS_PARAM, focus);
  }
  const hash = typeof link.hash === 'string' ? link.hash.replace(/^#/, '') : '';
  if (hash && !hashAllowed(spec, hash)) return null;
  const search = params.toString();
  return `${path.split(/[?#]/)[0]}${search ? `?${search}` : ''}${hash ? `#${hash}` : ''}`;
}

/** A section anchor the page knows by name, or by shape (a Bible verse: `v16`). */
export function hashAllowed(spec: RouteSpec, hash: string): boolean {
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(hash)) return false;
  if (spec.hashes.includes(hash)) return true;
  return Boolean(spec.hash_pattern) && new RegExp(spec.hash_pattern as string).test(hash);
}

/** Where a notice (from the bell, a push, the phone) opens: its link, else its in-app url. */
export function noticeHref(data: unknown): string | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as { link?: unknown; url?: unknown; path?: unknown };
  return linkHref(d.link) ?? inAppPath(d.url) ?? inAppPath(d.path);
}

/** The route an in-app address opens, with the guard it is under (`member` when none is stricter). */
export function routeOf(href: string): { route: string; guard: string } | null {
  const path = inAppPath(href);
  if (!path) return null;
  const url = new URL(path, 'http://app.invalid');
  for (const [name, spec] of Object.entries(NOTICE_ROUTES)) {
    if (!matchRoute(spec, url.pathname.replace(/\/$/, '') || '/')) continue;
    const keys = [...url.searchParams.keys()].filter((k) => k !== FOCUS_PARAM);
    if (!keys.every((k) => spec.query.includes(k))) continue;
    let guard = spec.guard;
    for (const [key, strict] of Object.entries(spec.query_guards)) if (url.searchParams.has(key)) guard = strict;
    return { route: name, guard };
  }
  return null;
}

/** The selector of the element a page marks with `data-focus-id`. */
export function focusSelector(focus: string): string {
  const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(focus) : focus.replace(/["\\]/g, '\\$&');
  return `[data-focus-id="${escaped}"]`;
}

const NOUNS: Record<string, string> = {
  msg: 'message',
  mission: 'mission',
  unlock: 'unlock request',
  guard: 'request',
  time: 'request for more time',
  chore: 'chore check',
  concern: 'alert',
  app: 'app',
  recap: 'recap',
  plant: 'plant',
  discovery: 'device',
  entry: 'integration',
  report: 'report',
  section: 'section',
  voice: 'set of recordings',
};

/** What the focused item is called, for "That message is no longer here." */
export function focusNoun(focus: string): string {
  const kind = FOCUS_RE.exec(focus)?.[1] ?? '';
  return NOUNS[kind] ?? 'item';
}

/** Every focus kind a route can show has a word for it (tested). */
export const FOCUS_NOUNS = NOUNS;

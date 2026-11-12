import { lazy, Suspense, useEffect, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { Home, Lock, MessageCircle } from 'lucide-react';
import {
  blockExternalNavigation,
  lockedNav,
  lockedPathAllowed,
  type LockedSectionId,
} from '../../lib/lockedMode';
import type { IWidgetProps } from '../../types/widget';

const ScreenTimeCard = lazy(() => import('../health/ScreenTimeCard'));
const ChoresProgressWidget = lazy(() => import('../widgets/ChoresProgressWidget'));
const DeviceControlWidget = lazy(() => import('../widgets/DeviceControlWidget'));

/** The dashboard widgets' props, with nothing to configure: locked mode has no settings. */
const widgetProps = (key: string): IWidgetProps => ({
  settingsButton: null,
  userSettings: {
    widget_key: key,
    visibility: 'visible',
    order_index: 0,
    size: 'medium',
    is_pinned: false,
    sort_mode: null,
    pinned_devices: [],
    config: {},
    updated_at: 0,
  },
  onTogglePin: () => undefined,
});

/**
 * The app while Jarvis is open from the screen-time lock: a plain top bar
 * saying the phone is locked, the open sections' own navigation, and either
 * the page (when its section is open) or a friendly locked screen. The usual
 * header and menus (settings, admin, the page finder) are not shown at all.
 */
export default function LockedShell({ children, sections }: { children: ReactNode; sections: LockedSectionId[] }) {
  const location = useLocation();
  useEffect(() => blockExternalNavigation(), []);

  const home = location.pathname === '/';
  const allowed = lockedPathAllowed(location.pathname, location.search, sections);
  const body = home ? <LockedHome sections={sections} /> : allowed ? children : <LockedPage />;
  const nav = lockedNav(sections);

  return (
    <div className="fixed inset-0 flex flex-col overflow-hidden app-shell" data-testid="locked-shell">
      <div className="safe-area-top app-bar z-40 border-b border-white/5">
        <div className="flex h-12 items-center gap-2 px-4 text-sm font-semibold text-slate-200">
          <Lock size={16} className="text-sky-300" aria-hidden />
          <span>Your phone is locked. Jarvis is still here.</span>
        </div>
      </div>
      <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 pb-clear-bottom-bar">{body}</main>
      <nav
        aria-label="Locked mode"
        className="fixed bottom-0 left-0 right-0 z-50 app-bar border-t"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
        data-testid="locked-nav"
      >
        <div className="mx-auto flex h-16 w-full max-w-lg items-center justify-around px-1">
          <NavItem to="/" label="Home" icon={Home} end />
          {nav.map((s) => (
            <NavItem key={s.id} to={s.to as string} label={s.label} icon={s.icon} />
          ))}
        </div>
      </nav>
    </div>
  );
}

function NavItem({ to, label, icon: Icon, end }: { to: string; label: string; icon: typeof Home; end?: boolean }) {
  const location = useLocation();
  // "/family?tab=calls" and "/family" are one path: compare the tab too.
  const [path, query] = to.split('?');
  const active = end
    ? location.pathname === '/'
    : location.pathname.startsWith(path) && (new URLSearchParams(query).get('tab') || '') === (new URLSearchParams(location.search).get('tab') || '');
  return (
    <NavLink
      to={to}
      end={end}
      className={`flex min-w-0 flex-1 flex-col items-center justify-center gap-1 py-1.5 text-[11px] ${active ? 'text-purple-300' : 'text-slate-400'}`}
      aria-current={active ? 'page' : undefined}
    >
      <Icon size={20} aria-hidden />
      <span className="truncate">{label}</span>
    </NavLink>
  );
}

/** The locked mode's home: chat first, then the cards for the sections that live here. */
export function LockedHome({ sections }: { sections: LockedSectionId[] }) {
  const open = new Set(sections);
  const pages = lockedNav(sections).filter((s) => s.id !== 'chat');
  return (
    <div className="mx-auto w-full max-w-3xl space-y-4" data-testid="locked-home">
      <Link
        to="/family"
        className="glass-panel flex min-h-16 items-center gap-3 rounded-2xl border border-white/10 p-4 text-white"
        data-testid="locked-home-chat"
      >
        <MessageCircle size={24} className="text-fuchsia-300" aria-hidden />
        <span className="flex-1">
          <span className="block text-base font-semibold">Talk to Jarvis</span>
          <span className="block text-xs text-slate-400">Ask a question, or message your family.</span>
        </span>
      </Link>
      {pages.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {pages.map((s) => (
            <Link
              key={s.id}
              to={s.to as string}
              className="flex min-h-12 items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-200"
              data-testid={`locked-home-${s.id}`}
            >
              <s.icon size={18} aria-hidden />
              {s.label}
            </Link>
          ))}
        </div>
      )}
      <Suspense fallback={null}>
        {open.has('time') && <div data-testid="locked-home-time"><ScreenTimeCard /></div>}
        {open.has('chores') && <div data-testid="locked-home-chores"><ChoresProgressWidget {...widgetProps('chores_progress')} /></div>}
        {open.has('devices') && <div data-testid="locked-home-devices"><DeviceControlWidget {...widgetProps('device_control')} /></div>}
      </Suspense>
    </div>
  );
}

/** Any page that is not open while the phone is locked. */
export function LockedPage() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-16 text-center" data-testid="locked-page">
      <Lock size={32} className="text-sky-300" aria-hidden />
      <h1 className="text-lg font-semibold text-white">This part is locked for now</h1>
      <p className="text-sm text-slate-400">
        Your phone is locked, so only some of Jarvis is open. You can still talk to Jarvis or ask a parent for more time.
      </p>
      <Link to="/family" className="glass-button inline-flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-semibold text-sky-100">
        <MessageCircle size={16} aria-hidden /> Back to chat
      </Link>
    </div>
  );
}

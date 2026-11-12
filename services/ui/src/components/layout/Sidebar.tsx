import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Activity, ChevronDown } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useServiceInfo, useSystemHealth } from '../../hooks/useSystemHealth';
import { cn } from '../../lib/utils';
import { navSectionsFor, sectionOfPath, type NavGroupKey, type NavPage } from './navItems';

const COLLAPSED_KEY = 'jarvis_sidebar_collapsed_v1';

function readCollapsed(): NavGroupKey[] {
  try {
    const raw = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.filter((v): v is NavGroupKey => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

const linkClass = (isActive: boolean) => cn(
  // Compact rows: fourteen pages and the status line must fit a 720px laptop
  // screen without the last links (Settings, Admin) scrolling out of sight.
  'flex items-center justify-center md:justify-start gap-3 px-3 md:px-4 py-2 rounded-xl transition-all duration-200',
  isActive
    ? 'bg-purple-600/30 text-white border border-purple-500/40 neon-border shadow-lg shadow-purple-500/10'
    : 'text-slate-400 hover:text-white hover:bg-white/5',
);

const SidebarLink = ({ item }: { item: NavPage }) => (
  <NavLink
    to={item.path}
    end={item.path === '/'}
    className={({ isActive }) => linkClass(isActive)}
    title={item.label}
  >
    <item.icon size={20} className="shrink-0" />
    <span className="font-medium hidden md:inline">{item.label}</span>
  </NavLink>
);

const Sidebar = () => {
  const { user } = useAuth();
  const isAdmin = user?.is_admin ?? false;
  const sections = navSectionsFor(isAdmin);
  const { pathname } = useLocation();
  const activeSection = sectionOfPath(sections, pathname);
  // Sections a person folded away stay folded on this device; the one holding
  // the open page is always shown, so the highlighted link is never hidden.
  const [collapsed, setCollapsed] = useState<NavGroupKey[]>(readCollapsed);
  const toggle = (key: NavGroupKey) => {
    setCollapsed((current) => {
      const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key];
      try {
        localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next));
      } catch {
        // Storage can be blocked; the fold still works for this session.
      }
      return next;
    });
  };

  return (
    <aside className="w-20 md:w-64 glass-sidebar m-2 md:m-4 md:mr-0 flex flex-col transition-all duration-300 rounded-2xl">
      <div className="p-4 md:px-6 md:py-5 flex justify-center md:justify-start">
        <h1 className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent flex items-center gap-2">
          <Activity className="text-purple-400" />
          <span className="hidden md:inline">Jarvis OS</span>
        </h1>
      </div>

      <nav className="flex-1 px-2 md:px-4 overflow-y-auto" aria-label="Pages">
        {sections.map((section, index) => {
          const open = section.key === activeSection || !collapsed.includes(section.key);
          const listId = `nav-section-${section.key}`;
          return (
            <div key={section.key} data-testid={`nav-section-${section.key}`} className={index > 0 ? 'mt-2' : ''}>
              {index > 0 && (
                <>
                  {/* Icon-only rail: a rule between sections instead of a heading. */}
                  <div className="border-t border-white/10 my-2 md:hidden" aria-hidden />
                  <button
                    type="button"
                    onClick={() => toggle(section.key)}
                    aria-expanded={open}
                    aria-controls={listId}
                    className="hidden md:flex w-full min-h-8 items-center justify-between px-4 text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-slate-300"
                  >
                    {section.label}
                    <ChevronDown size={12} className={cn('transition-transform', open ? '' : '-rotate-90')} aria-hidden />
                  </button>
                </>
              )}
              <div id={listId} className={cn('space-y-1', open ? '' : 'md:hidden')}>
                {section.pages.map((item) => <SidebarLink key={item.path} item={item} />)}
              </div>
            </div>
          );
        })}
      </nav>

      {/* Raven's status lives on the dashboard and in the Lab; a third copy
          here pushed the nav off short screens. */}
      <SystemStatusFooter />
    </aside>
  );
};

/** Real gateway readiness and build, not a fixed "All Services Nominal". */
const SystemStatusFooter = () => {
  const { user } = useAuth();
  const isAdmin = user?.is_admin ?? false;
  const { data: health, isError } = useSystemHealth();
  const { data: info } = useServiceInfo();
  const down = health ? Object.values(health.services ?? {}).filter((s) => s !== 'OK').length : 0;
  const [dot, text] = isError
    ? ['bg-red-500', 'Server unreachable']
    : !health
      ? ['bg-yellow-500', 'Checking services…']
      : down === 0
        ? ['bg-green-500', 'All services online']
        : ['bg-amber-500', `${down} service${down === 1 ? '' : 's'} degraded`];

  return (
    <div className="px-4 pb-4 pt-2 mt-auto hidden md:block">
      <div className="glass-card px-4 py-2.5 text-xs text-slate-500 flex items-center gap-2" title={isAdmin && info?.git_sha && info.git_sha !== 'unknown' ? `Build ${info.git_sha}` : undefined}>
        <div className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
        <span className="truncate">{text}</span>
        {/* The build hash is for whoever is looking after the server, not for
            the family using it. */}
        {isAdmin && info && info.git_sha && info.git_sha !== 'unknown' && (
          <span className="ml-auto font-mono text-[10px] text-slate-600">{info.git_sha}</span>
        )}
      </div>
    </div>
  );
};

export default Sidebar;

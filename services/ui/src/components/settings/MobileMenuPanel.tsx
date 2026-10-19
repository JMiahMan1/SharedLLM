import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Pin, PinOff, RotateCcw, Smartphone } from 'lucide-react';
import toast from 'react-hot-toast';
import { useAuth } from '../../context/AuthContext';
import { navPagesFor, type NavPage } from '../layout/navItems';
import {
  MAX_MOBILE_PINS,
  moveMobileNavPath,
  readMobileNavPaths,
  resetMobileNavPaths,
  sanitizeMobileNavPaths,
  subscribeMobileNav,
  toggleMobileNavPath,
  writeMobileNavPaths,
} from '../../lib/mobileNav';

/**
 * Per-device choice of the pages pinned to the phone's bottom bar. Only paths
 * are stored; the pages themselves come from `navPagesFor`, so admin-only
 * pages never appear for ordinary members and a removed route disappears
 * instead of leaving a dead tab.
 */
export default function MobileMenuPanel() {
  const { user } = useAuth();
  const isAdmin = user?.is_admin ?? false;
  const [paths, setPaths] = useState(() => readMobileNavPaths(isAdmin));

  useEffect(() => {
    const sync = () => setPaths(readMobileNavPaths(isAdmin));
    sync();
    return subscribeMobileNav(sync);
  }, [isAdmin]);

  const pages = navPagesFor(isAdmin);
  const pageByPath = new Map(pages.map((page) => [page.path, page]));
  const pinned = paths.flatMap((path): NavPage[] => {
    const page = pageByPath.get(path);
    return page ? [page] : [];
  });
  const pinnedSet = new Set(pinned.map((page) => page.path));
  const others = pages.filter((page) => !pinnedSet.has(page.path));
  const full = pinned.length >= MAX_MOBILE_PINS;

  const commit = (next: string[]) => {
    setPaths(writeMobileNavPaths(next, isAdmin));
  };

  const pin = (path: string) => {
    const next = toggleMobileNavPath(paths, path, isAdmin);
    if (next.length === paths.length && !paths.includes(path)) {
      toast.error(`The bottom bar holds ${MAX_MOBILE_PINS} pages. Unpin one first.`);
      return;
    }
    commit(next);
  };

  const unpin = (path: string) => {
    commit(toggleMobileNavPath(paths, path, isAdmin));
  };

  const move = (path: string, direction: -1 | 1) => {
    commit(moveMobileNavPath(sanitizeMobileNavPaths(paths, isAdmin), path, direction));
  };

  const reset = () => {
    setPaths(resetMobileNavPaths(isAdmin));
    toast.success('Mobile menu reset to the default pages');
  };

  return (
    <section
      className="glass-panel rounded-2xl p-4 space-y-3"
      aria-labelledby="mobile-menu-heading"
      data-testid="mobile-menu-panel"
    >
      <div className="flex items-start justify-between gap-3 px-1">
        <div>
          <h2
            id="mobile-menu-heading"
            className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2"
          >
            <Smartphone size={16} className="text-purple-400" />
            Mobile menu
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Pick up to {MAX_MOBILE_PINS} pages for the bottom bar on this device. Everything else
            stays in More. Changes apply immediately and are not synced to other devices.
          </p>
        </div>
        <button
          type="button"
          onClick={reset}
          className="glass-button shrink-0 px-3 py-1.5 rounded-xl text-xs font-semibold text-purple-300 hover:text-white flex items-center gap-1.5 border-purple-500/30 pointer-coarse:min-h-11"
        >
          <RotateCcw size={13} />
          Reset to defaults
        </button>
      </div>

      <div className="px-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          Pinned · {pinned.length}/{MAX_MOBILE_PINS}
        </p>
      </div>

      <div className="space-y-1">
        {pinned.length === 0 && (
          <p className="px-3 py-4 rounded-xl bg-white/5 text-xs text-slate-400">
            No pages pinned. The More button still opens every page.
          </p>
        )}
        {pinned.map((page, index) => (
          <div
            key={page.path}
            className="flex items-center gap-2 p-2.5 rounded-xl bg-white/5"
            data-testid={`mobile-pin-${page.path}`}
          >
            <span className="w-5 text-center text-[11px] font-mono text-slate-500 shrink-0">
              {index + 1}
            </span>
            <page.icon size={18} className="text-purple-300 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-white text-sm font-medium truncate">{page.label}</p>
              {page.mobileLabel && page.mobileLabel !== page.label && (
                <p className="text-[11px] text-slate-500">Shown as “{page.mobileLabel}”</p>
              )}
            </div>
            <button
              type="button"
              aria-label={`Move ${page.label} up`}
              onClick={() => move(page.path, -1)}
              disabled={index === 0}
              className="w-9 h-9 pointer-coarse:w-11 pointer-coarse:h-11 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 disabled:opacity-30 disabled:hover:bg-white/5 flex items-center justify-center"
            >
              <ArrowUp size={15} />
            </button>
            <button
              type="button"
              aria-label={`Move ${page.label} down`}
              onClick={() => move(page.path, 1)}
              disabled={index === pinned.length - 1}
              className="w-9 h-9 pointer-coarse:w-11 pointer-coarse:h-11 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 disabled:opacity-30 disabled:hover:bg-white/5 flex items-center justify-center"
            >
              <ArrowDown size={15} />
            </button>
            <button
              type="button"
              aria-label={`Unpin ${page.label}`}
              onClick={() => unpin(page.path)}
              className="h-9 px-2.5 pointer-coarse:h-11 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 flex items-center gap-1.5 text-xs font-medium"
            >
              <PinOff size={14} />
              Unpin
            </button>
          </div>
        ))}
      </div>

      <div className="px-1 pt-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          Other pages
        </p>
      </div>

      <div className="space-y-1">
        {others.map((page) => (
          <div
            key={page.path}
            className="flex items-center gap-3 p-2.5 rounded-xl bg-white/5"
            data-testid={`mobile-other-${page.path}`}
          >
            <page.icon size={18} className="text-slate-400 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-white text-sm font-medium truncate">{page.label}</p>
              {page.adminOnly && <p className="text-[11px] text-amber-400/80">Admin only</p>}
            </div>
            <button
              type="button"
              aria-label={`Pin ${page.label}`}
              onClick={() => pin(page.path)}
              disabled={full}
              title={full ? `Unpin a page first (${MAX_MOBILE_PINS} max)` : undefined}
              className="h-9 px-2.5 pointer-coarse:h-11 rounded-lg bg-purple-500/15 border border-purple-500/25 text-purple-300 hover:bg-purple-500/25 disabled:opacity-30 disabled:hover:bg-purple-500/15 flex items-center gap-1.5 text-xs font-medium"
            >
              <Pin size={14} />
              Pin
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

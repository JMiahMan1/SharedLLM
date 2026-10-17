import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Bell,
  BellOff,
  BookOpen,
  BookOpenText,
  ChevronRight,
  Copy,
  ExternalLink,
  Flame,
  Sparkles,
} from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleVerseOfDay } from '../../types/api';
import BiblePlans from './BiblePlans';
import VerseContextPanel from './VerseContextPanel';
import { pageStyle } from './readingPage';

interface BibleTodayProps {
  onOpenRef: (ref: string) => void;
  /** The reader's page tone, so the verse reads the same as in the reader. */
  page?: string;
  /** Daily reminder time ("HH:MM"), empty when off. */
  reminderTime?: string;
  onReminderTime?: (value: string) => void;
}

/**
 * Verse of the Day plus today's devotional, the way the Bible App puts them at
 * the top of Home.
 *
 * A devotional source that is not configured is reported here with the setting
 * to fix rather than leaving an empty card -- an empty devotional looks like a
 * bug and reads as a broken product.
 */
export default function BibleToday({ onOpenRef, page, reminderTime = '', onReminderTime }: BibleTodayProps) {
  const { trigger } = useHaptics();
  const { data, isLoading, error } = useQuery({
    queryKey: ['bible-daily'],
    queryFn: () => api.getBibleDaily(),
    staleTime: 30 * 60 * 1000,
    retry: 1,
  });

  const failure = error instanceof Error ? error.message : null;
  const vod = data?.verse_of_day;
  const verse = vod && !('error' in vod) ? vod : null;
  const verseError = vod && 'error' in vod ? vod.error : null;
  const entry = data?.devotional?.entry ?? null;
  const skipped = data?.devotional?.skipped ?? [];

  // The verse card's press-and-hold menu. `longPressed` is read by the click
  // that follows a release so opening the menu never also opens the passage.
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [contextVerse, setContextVerse] = useState<BibleVerseOfDay | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const pressTimer = useRef<number | null>(null);
  const pressReset = useRef<number | null>(null);
  const pressStart = useRef<{ x: number; y: number } | null>(null);
  const longPressed = useRef(false);

  const clearPressTimer = () => {
    if (pressTimer.current !== null) {
      window.clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
    pressStart.current = null;
  };

  useEffect(
    () => () => {
      if (pressTimer.current !== null) window.clearTimeout(pressTimer.current);
      if (pressReset.current !== null) window.clearTimeout(pressReset.current);
    },
    [],
  );

  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenu(null);
        return;
      }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
      if (items.length === 0) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      items[current === -1 ? 0 : (current + step + items.length) % items.length]?.focus();
    };
    document.addEventListener('keydown', onKey);
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [menu]);

  const openMenuAt = (x: number, y: number) => {
    // First-paint clamp: three actions are about 180px tall, and a phone's
    // bottom edge must not cut the last one off.
    const width = 216;
    const height = 190;
    setMenu({
      x: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - height - 8)),
    });
  };

  const startPress = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    clearPressTimer();
    longPressed.current = false;
    pressStart.current = { x: event.clientX, y: event.clientY };
    pressTimer.current = window.setTimeout(() => {
      pressTimer.current = null;
      longPressed.current = true;
      void trigger('medium');
      openMenuAt(event.clientX, event.clientY);
    }, 500);
  };

  const movePress = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const start = pressStart.current;
    if (!start) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) clearPressTimer();
  };

  const endPress = () => {
    clearPressTimer();
    if (!longPressed.current) return;
    // Clear after the click that follows this release has been suppressed. The
    // timer is a fallback for browsers that do not send that click at all.
    pressReset.current = window.setTimeout(() => {
      longPressed.current = false;
      pressReset.current = null;
    }, 400);
  };

  return (
    <div className="space-y-4" data-testid="bible-today">
      <section className="glass-panel rounded-2xl p-4 sm:p-5 border border-amber-400/15">
        <div className="flex items-center gap-2 mb-3">
          <BookOpen size={16} className="text-amber-300" />
          <h2 className="text-sm font-bold text-white">Verse of the Day</h2>
          {data?.streaks && (
            <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-slate-400">
              <Flame
                size={13}
                className={data.streaks.read_streak_current ? 'text-orange-400' : 'text-slate-600'}
              />
              {data.streaks.read_streak_current} day streak
            </span>
          )}
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <div className="skeleton h-3 w-28 rounded" />
            <div className="skeleton h-4 w-full rounded" />
            <div className="skeleton h-4 w-3/4 rounded" />
          </div>
        ) : failure || verseError ? (
          <p className="text-sm text-amber-300/90 leading-relaxed">{failure ?? verseError}</p>
        ) : verse ? (
          <button
            type="button"
            data-testid="bible-votd"
            aria-haspopup="menu"
            title="Press and hold for verse options"
            onPointerDown={startPress}
            onPointerMove={movePress}
            onPointerUp={endPress}
            onPointerCancel={endPress}
            onPointerLeave={clearPressTimer}
            onContextMenu={(event) => {
              event.preventDefault();
              clearPressTimer();
              openMenuAt(event.clientX, event.clientY);
            }}
            onClick={() => {
              if (longPressed.current) {
                longPressed.current = false;
                if (pressReset.current !== null) {
                  window.clearTimeout(pressReset.current);
                  pressReset.current = null;
                }
                return;
              }
              void trigger('light');
              onOpenRef(verse.reference);
            }}
            className="w-full text-left min-h-11 pointer-coarse:min-h-11 rounded-xl px-4 py-3 border border-[var(--page-rule)] transition-colors select-none"
            style={pageStyle(page)}
          >
            <p className="text-xs font-semibold text-[var(--page-number)]">{verse.reference}</p>
            <p className="mt-2 font-serif text-lg sm:text-xl leading-relaxed text-[var(--page-ink)]">{verse.text}</p>
          </button>
        ) : (
          <p className="text-sm text-slate-400">No verse available for today.</p>
        )}
      </section>

      {data?.position?.reference && (
        <button
          type="button"
          onClick={() => {
            void trigger('light');
            onOpenRef(data.position?.reference ?? '');
          }}
          data-testid="bible-continue"
          className="w-full glass-panel rounded-2xl p-4 border border-white/10 flex items-center gap-3 text-left min-h-11 hover:border-amber-400/40"
        >
          <BookOpenText size={18} className="text-amber-300 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="block text-[11px] uppercase tracking-wider text-slate-500">Continue reading</span>
            <span className="block text-base font-serif text-white">{data.position.reference}</span>
          </span>
          <ChevronRight size={16} className="text-slate-500" />
        </button>
      )}

      <BiblePlans onOpenRef={onOpenRef} />

      {onReminderTime && (
        <section className="glass-panel rounded-2xl p-4 border border-white/10 flex flex-wrap items-center gap-3" data-testid="bible-reminder">
          {reminderTime ? <Bell size={16} className="text-amber-300" /> : <BellOff size={16} className="text-slate-500" />}
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-white">Daily reminder</p>
            <p className="text-xs text-slate-400">
              {reminderTime
                ? "If you haven't read by then, Jarvis sends one nudge with today's reading."
                : 'Off. Pick a time to get one nudge on days you have not read yet.'}
            </p>
          </div>
          <input
            type="time"
            value={reminderTime}
            onChange={(e) => onReminderTime(e.target.value)}
            aria-label="Reminder time"
            data-testid="bible-reminder-time"
            className="min-h-11 rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white"
          />
          {reminderTime && (
            <button
              type="button"
              onClick={() => onReminderTime('')}
              className="min-h-11 rounded-xl px-3 text-xs text-slate-300 hover:bg-white/5"
              data-testid="bible-reminder-off"
            >
              Turn off
            </button>
          )}
        </section>
      )}

      <section className="glass-panel rounded-2xl p-4 sm:p-5 border border-white/10">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={16} className="text-purple-300" />
          <h2 className="text-sm font-bold text-white">Today's devotional</h2>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <div className="skeleton h-3 w-40 rounded" />
            <div className="skeleton h-3 w-full rounded" />
          </div>
        ) : entry ? (
          <div className="space-y-2">
            {entry.reference && (
              <button
                type="button"
                onClick={() => onOpenRef(entry.reference)}
                className="text-xs font-semibold text-amber-300/90 min-h-11 pointer-coarse:min-h-11"
              >
                {entry.reference}
              </button>
            )}
            <p className="font-serif text-base leading-relaxed text-slate-200">
              {entry.kind === 'text' ? entry.text : entry.title}
            </p>
            {entry.kind === 'link' && entry.url && (
              <button
                type="button"
                onClick={() => {
                  void trigger('light');
                  window.open(entry.url, '_blank', 'noopener,noreferrer');
                }}
                data-testid="bible-devotional-open"
                className="glass-button px-3 py-2.5 text-xs min-h-11 inline-flex items-center gap-1.5"
              >
                Read at {data?.devotional.source === 'blb' ? 'Blue Letter Bible' : entry.source}
                <ExternalLink size={13} />
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-slate-400">
              {data?.devotional.reason ?? 'No devotional is available for today.'}
            </p>
            {skipped.length > 0 && (
              <ul className="space-y-1" data-testid="bible-devotional-skipped">
                {skipped.map((s) => (
                  <li key={s.source} className="text-xs text-slate-500 flex items-start gap-1.5">
                    <AlertTriangle size={12} className="mt-0.5 shrink-0 text-amber-400/70" />
                    {s.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {menu &&
        verse &&
        createPortal(
          <>
            <div
              className="fixed inset-0 z-40 bg-slate-950/30 backdrop-blur-sm"
              onClick={() => setMenu(null)}
              data-testid="bible-votd-menu-scrim"
            />
            <div
              ref={menuRef}
              style={{ top: menu.y, left: menu.x }}
              className="fixed z-50 glass-card min-w-[200px] max-h-[calc(100dvh-16px)] overflow-y-auto overscroll-contain p-2 animate-in fade-in"
              role="menu"
              aria-label={`${verse.reference} options`}
              data-testid="bible-votd-menu"
            >
              <div className="px-2 py-1 mb-1 text-xs font-semibold text-white">{verse.reference}</div>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  void trigger('light');
                  setMenu(null);
                  setContextVerse(verse);
                }}
                className="w-full text-left text-xs px-2 py-1.5 rounded-md min-h-11 pointer-coarse:min-h-11 hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors flex items-center gap-2"
                data-testid="bible-votd-context"
              >
                <Sparkles size={13} />
                Get context
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  void trigger('light');
                  setMenu(null);
                  onOpenRef(verse.reference);
                }}
                className="w-full text-left text-xs px-2 py-1.5 rounded-md min-h-11 pointer-coarse:min-h-11 hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors flex items-center gap-2"
              >
                <BookOpenText size={13} />
                Read passage
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  void trigger('success');
                  if (navigator.clipboard) {
                    void navigator.clipboard.writeText(`${verse.reference}\n${verse.text}`).catch(() => {});
                  }
                  setMenu(null);
                }}
                className="w-full text-left text-xs px-2 py-1.5 rounded-md min-h-11 pointer-coarse:min-h-11 hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors flex items-center gap-2"
              >
                <Copy size={13} />
                Copy verse
              </button>
            </div>
          </>,
          document.body,
        )}

      {contextVerse && (
        <VerseContextPanel
          verse={contextVerse}
          onClose={() => setContextVerse(null)}
          onOpenRef={onOpenRef}
        />
      )}
    </div>
  );
}
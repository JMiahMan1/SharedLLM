import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Calendar as CalIcon,
  Plus,
  Settings as SettingsIcon,
  Check,
  X,
  ChevronLeft,
  ChevronRight,
  MapPin,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useDarkModeSync } from '../../hooks/useDarkModeSync';
import { api } from '../../services/api';
import { integrationMeta } from './integrationMeta';
import type { CalendarEvent } from '../../types/widget';
import type { ExecutionResponse } from '../../services/api';

// ─── Date helpers (native, no luxon) ──────────────────────────────────────
const DAY_MS = 86_400_000;
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const startOfDay = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const startOfWeek = (d: Date) => { const x = startOfDay(d); x.setDate(x.getDate() - x.getDay()); return x; }; // Sunday
const startOfMonthGrid = (d: Date) => startOfWeek(new Date(d.getFullYear(), d.getMonth(), 1));
const isAllDay = (e: CalendarEvent) => {
  const s = new Date(e.start_time);
  const en = e.end_time ? new Date(e.end_time) : s;
  const sd = startOfDay(s);
  const ed = startOfDay(en);
  return s.getHours() === 0 && s.getMinutes() === 0 && (sd.getTime() === ed.getTime() || !e.end_time);
};

const formatTime = (iso: string): string => {
  const d = new Date(iso);
  let h = d.getHours();
  const m = d.getMinutes();
  const period = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${m.toString().padStart(2, '0')} ${period}`;
};

// Pick readable text color on a fill
const textOn = (hex: string): string => {
  const c = hex.replace('#', '');
  const r = parseInt(c.slice(0, 2), 16);
  const g = parseInt(c.slice(2, 4), 16);
  const b = parseInt(c.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.62 ? '#34302a' : '#fffdf8';
};

type ViewKind = 'agenda' | 'day' | 'week' | 'month';

interface IntegrationInfo {
  type: string;
  enabled: boolean;
  writable: boolean;
  provides_calendar: boolean;
  urls?: string[];
}
interface CalendarDetail {
  integrations: IntegrationInfo[];
  default?: string;
  needs_default_choice?: boolean;
  available_defaults?: string[];
}

// Build a map of dayKey -> events that overlap that day (within [rangeStart, rangeEnd))
const buildByDay = (events: CalendarEvent[], rangeStart: Date, rangeEnd: Date) => {
  const map = new Map<string, CalendarEvent[]>();
  const rs = startOfDay(rangeStart).getTime();
  const re = startOfDay(rangeEnd).getTime();
  for (const ev of events) {
    const evStart = startOfDay(new Date(ev.start_time)).getTime();
    const evEnd = startOfDay(ev.end_time ? new Date(ev.end_time) : new Date(ev.start_time)).getTime();
    const lo = Math.max(evStart, rs);
    const hi = Math.min(evEnd, re - DAY_MS);
    if (lo > hi) continue;
    for (let t = lo; t <= hi; t += DAY_MS) {
      const key = ymd(new Date(t));
      const arr = map.get(key) ?? [];
      arr.push(ev);
      map.set(key, arr);
    }
  }
  for (const arr of map.values()) {
    arr.sort((a, b) =>
      isAllDay(a) === isAllDay(b)
        ? (a.start_time < b.start_time ? -1 : 1)
        : isAllDay(a) ? -1 : 1
    );
  }
  return map;
};

// Warm "paper-planner" (OpenSkyLight) palette — used in light mode.
const LIGHT_VARS = `
  --os-paper:#f5efe3;
  --os-paper-deep:#ece4d2;
  --os-card:#fffdf8;
  --os-ink:#34302a;
  --os-ink-soft:#756d5f;
  --os-ink-faint:#a89f8d;
  --os-line:#e3dac6;
  --os-ember:#d95b3a;
  --os-ember-deep:#bf4526;
  --os-ember-soft:#f8ddd2;
  --os-sun:#ffd9a0;
  --os-sun-soft:#fdf0da;
  --os-shadow:0 1px 3px rgba(72,60,38,0.07),0 10px 28px -10px rgba(72,60,38,0.16);
  --os-input-bg:#fffdf8;
  --os-panel-bg:rgba(255,253,248,0.6);
  --os-body-font:'Nunito',ui-sans-serif,system-ui,sans-serif;
  --os-display:'Fraunces',Georgia,'Times New Roman',serif;
`;

// Glass / neon palette — matches the rest of the SharedLLM site (dark default).
const DARK_VARS = `
  --os-paper:rgba(255,255,255,0.03);
  --os-paper-deep:rgba(255,255,255,0.07);
  --os-card:rgba(255,255,255,0.06);
  --os-ink:#f1f5f9;
  --os-ink-soft:rgba(241,245,249,0.62);
  --os-ink-faint:rgba(241,245,249,0.38);
  --os-line:rgba(255,255,255,0.12);
  --os-ember:#8b5cf6;
  --os-ember-deep:#c4b5fd;
  --os-ember-soft:rgba(139,92,246,0.15);
  --os-sun:#a78bfa;
  --os-sun-soft:rgba(139,92,246,0.14);
  --os-shadow:0 4px 24px rgba(0,0,0,0.45),0 1px 0 rgba(255,255,255,0.04) inset;
  --os-input-bg:rgba(0,0,0,0.25);
  --os-panel-bg:rgba(255,255,255,0.05);
  --os-body-font:'Outfit',ui-sans-serif,system-ui,sans-serif;
  --os-display:'Outfit',ui-sans-serif,system-ui,sans-serif;
`;

// ─── Warm EventCard ──────────────────────────────────────────────────────────
const EventCard = ({ ev, size = 'md' }: { ev: CalendarEvent; size?: 'md' | 'lg' }) => {
  const meta = integrationMeta(ev.integration);
  const color = meta.color;
  const allDay = isAllDay(ev);
  if (allDay) {
    return (
      <button
        type="button"
        className={`w-full text-left font-bold shadow-[0_1px_3px_rgba(72,60,38,0.07),0_10px_28px_-10px_rgba(72,60,38,0.16)] ${size === 'lg' ? 'min-h-14 rounded-2xl px-4 py-3 text-lg' : 'min-h-11 rounded-xl px-3 py-2 text-[15px]'}`}
        style={{ backgroundColor: color, color: textOn(color) }}
      >
        <span className="truncate">{ev.summary}</span>
      </button>
    );
  }
  return (
    <button
      type="button"
      className={`w-full text-left shadow-[0_1px_3px_rgba(72,60,38,0.07),0_10px_28px_-10px_rgba(72,60,38,0.16)] ${size === 'lg' ? 'rounded-2xl p-4' : 'rounded-xl px-3 py-2'}`}
      style={{ backgroundColor: 'var(--os-card)', borderLeft: `5px solid ${color}` }}
    >
      <div className="flex items-center gap-1.5">
        <span className={`font-extrabold ${size === 'lg' ? 'text-base' : 'text-[13px]'}`} style={{ color }}>{formatTime(ev.start_time)}</span>
      </div>
      <div className={`truncate font-bold ${size === 'lg' ? 'text-xl' : 'text-[15px]'}`} style={{ color: 'var(--os-ink)' }}>{ev.summary}</div>
      {size === 'lg' && ev.location && (
        <div className="mt-1 flex items-center gap-1 text-sm font-semibold" style={{ color: 'var(--os-ink-soft)' }}>
          <MapPin size={14} />
          <span className="truncate">{ev.location}</span>
        </div>
      )}
    </button>
  );
};

const CalendarApp = () => {
  const { isDark } = useDarkModeSync();
  const queryClient = useQueryClient();
  const [view, setView] = useState<ViewKind>('agenda');
  const [focused, setFocused] = useState<Date>(() => new Date());
  const [activeIntegration, setActiveIntegration] = useState<string>('all');
  const [showSources, setShowSources] = useState(false);
  const [summary, setSummary] = useState('');
  const [startInput, setStartInput] = useState('');
  const [addIntegration, setAddIntegration] = useState('');

  const { data, isLoading, error } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-app'],
    queryFn: () => api.getCalendarEvents(),
    refetchInterval: 300_000,
  });
  const { data: settingsData } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-settings'],
    queryFn: () => api.getCalendarSettings(),
  });

  const detail = data?.detail as CalendarDetail | undefined;
  const integrations = detail?.integrations ?? [];
  const allEvents = useMemo<CalendarEvent[]>(
    () => (data?.events as CalendarEvent[] | undefined) ?? [],
    [data]
  );
  const visibleEvents = useMemo(
    () => (activeIntegration === 'all' ? allEvents : allEvents.filter((e) => (e.integration ?? '') === activeIntegration)),
    [allEvents, activeIntegration]
  );

  // View range
  const { rangeStart, rangeEnd, days } = useMemo(() => {
    const f = focused;
    if (view === 'day') {
      const s = startOfDay(f);
      return { rangeStart: s, rangeEnd: addDays(s, 1), days: [s] };
    }
    if (view === 'week') {
      const s = startOfWeek(f);
      return { rangeStart: s, rangeEnd: addDays(s, 7), days: Array.from({ length: 7 }, (_, i) => addDays(s, i)) };
    }
    if (view === 'month') {
      const s = startOfMonthGrid(f);
      const arr = Array.from({ length: 42 }, (_, i) => addDays(s, i));
      return { rangeStart: s, rangeEnd: addDays(s, 42), days: arr };
    }
    // agenda: 60 days from focused
    const s = startOfDay(f);
    const arr = Array.from({ length: 60 }, (_, i) => addDays(s, i));
    return { rangeStart: s, rangeEnd: addDays(s, 60), days: arr };
  }, [view, focused]);

  const byDay = useMemo(
    () => buildByDay(visibleEvents, rangeStart, rangeEnd),
    [visibleEvents, rangeStart, rangeEnd]
  );

  const shift = (dir: -1 | 1) => {
    if (view === 'day') setFocused((d) => addDays(d, dir));
    else if (view === 'week') setFocused((d) => addDays(d, dir * 7));
    else if (view === 'month') setFocused((d) => new Date(d.getFullYear(), d.getMonth() + dir, 1));
    else setFocused((d) => addDays(d, dir * 30));
  };
  const goToday = () => setFocused(new Date());

  const disabledSet = new Set(settingsData?.settings?.disabled ?? []);
  const writable = integrations.filter((i) => i.enabled && i.writable);

  const addMutation = useMutation({
    mutationFn: () => api.addCalendarEvent({
      summary: summary.trim(),
      start_time: startInput.trim(),
      integration: addIntegration || detail?.default || undefined,
    }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['calendar-app'] }); toast.success('Event added'); setSummary(''); setStartInput(''); },
    onError: (e: Error) => toast.error(e.message || 'Failed to add event'),
  });
  const setDefaultMutation = useMutation({
    mutationFn: (value: string) => api.updateCalendarSettings({ default: value }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['calendar-app'] }); toast.success('Default calendar updated'); },
    onError: (e: Error) => toast.error(e.message || 'Failed to update default'),
  });
  const toggleDisabledMutation = useMutation({
    mutationFn: (type: string) => {
      const next = new Set(disabledSet);
      if (next.has(type)) next.delete(type); else next.add(type);
      return api.updateCalendarSettings({ disabled: Array.from(next) });
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['calendar-settings'] }); queryClient.invalidateQueries({ queryKey: ['calendar-app'] }); },
    onError: (e: Error) => toast.error(e.message || 'Failed to update integration'),
  });
  const [icalUrl, setIcalUrl] = useState('');
  const addIcalMutation = useMutation({
    mutationFn: (url: string) => {
      const current = settingsData?.settings?.ical_urls ?? [];
      return api.updateCalendarSettings({ ical_urls: [...current, url] });
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['calendar-settings'] }); queryClient.invalidateQueries({ queryKey: ['calendar-app'] }); setIcalUrl(''); toast.success('iCal feed added'); },
    onError: (e: Error) => toast.error(e.message || 'Failed to add iCal feed'),
  });

  const openDay = (key: string) => { setFocused(new Date(key + 'T00:00:00')); setView('day'); };

  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const todayKey = ymd(new Date());

  const viewBtn = (key: ViewKind, label: string) => (
    <button
      key={key}
      onClick={() => setView(key)}
      className={`px-4 py-1.5 rounded-full text-xs font-bold transition ${view === key ? '' : 'hover:opacity-80'}`}
      style={view === key ? { background: 'var(--os-ember)', color: '#fffdf8' } : { color: 'var(--os-ink-soft)' }}
    >
      {label}
    </button>
  );

  // Today highlight styles (shared look; colors come from the active theme vars)
  const todayCell = (radius: string) => ({
    background: 'var(--os-sun-soft)',
    boxShadow: '0 0 0 2px var(--os-sun)',
    borderRadius: radius,
  });
  const todayNum = { background: 'var(--os-ember)', color: '#fffdf8' };

  return (
    <section className="os-calendar rounded-[1.25rem] p-4 md:p-6" style={{ background: 'var(--os-paper)' }}>
      <style>{`
${isDark ? DARK_VARS : LIGHT_VARS}
.os-calendar{
  font-family:var(--os-body-font); color:var(--os-ink);
}
.os-calendar .os-display{ font-family:var(--os-display); }
.os-calendar .os-card{ background:var(--os-card); box-shadow:var(--os-shadow); }
`}</style>

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <CalIcon size={20} style={{ color: 'var(--os-ember)' }} />
          <h3 className="os-display text-2xl font-bold" style={{ color: 'var(--os-ink)' }}>Calendar</h3>
        </div>
        <div className="flex items-center gap-1 rounded-full p-1" style={{ background: 'var(--os-paper-deep)' }}>
          {viewBtn('agenda', 'Agenda')}
          {viewBtn('day', 'Day')}
          {viewBtn('week', 'Week')}
          {viewBtn('month', 'Month')}
        </div>
        <button onClick={() => setShowSources(!showSources)} className="rounded-full px-3 py-1.5 text-[10px] font-black uppercase tracking-widest" style={{ background: 'var(--os-ember-soft)', color: 'var(--os-ember-deep)' }}>
          <SettingsIcon size={14} className="inline mr-1" />Sources
        </button>
      </div>

      <div className="mb-4 flex items-center gap-2">
        <button onClick={() => shift(-1)} className="os-card rounded-full p-2 transition hover:opacity-80" style={{ color: 'var(--os-ink-soft)' }}><ChevronLeft size={18} /></button>
        <button onClick={goToday} className="rounded-full px-3 py-1 text-xs font-bold" style={{ background: 'var(--os-sun-soft)', color: 'var(--os-ember-deep)' }}>Today</button>
        <button onClick={() => shift(1)} className="os-card rounded-full p-2 transition hover:opacity-80" style={{ color: 'var(--os-ink-soft)' }}><ChevronRight size={18} /></button>
        <div className="ml-2 os-display text-lg font-semibold" style={{ color: 'var(--os-ink-soft)' }}>
          {focused.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
        </div>
      </div>

      {detail?.needs_default_choice && (
        <div className="mb-4 rounded-2xl p-4" style={{ background: 'var(--os-ember-soft)', border: '1px solid var(--os-ember)' }}>
          <p className="text-sm font-bold" style={{ color: 'var(--os-ember-deep)' }}>Choose a default calendar</p>
          <p className="text-xs mb-2" style={{ color: 'var(--os-ember-deep)', opacity: 0.8 }}>
            You have more than one calendar source connected. Pick which one opens by default.
          </p>
          <div className="flex flex-wrap gap-2">
            {(detail.available_defaults ?? integrations.filter((i) => i.enabled).map((i) => i.type)).map((t) => (
              <button key={t} onClick={() => setDefaultMutation.mutate(t)} className="rounded-full border px-3 py-1.5 text-xs font-bold" style={{ borderColor: 'var(--os-ember)', background: 'var(--os-ember-soft)', color: 'var(--os-ember-deep)' }}>
                {integrationMeta(t).label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Integration chips */}
      <div className="mb-5 flex flex-wrap gap-2">
        <button onClick={() => setActiveIntegration('all')} className={`rounded-full border px-4 py-1.5 text-xs font-bold transition ${activeIntegration === 'all' ? '' : 'hover:opacity-80'}`} style={activeIntegration === 'all' ? { background: 'var(--os-ember)', borderColor: 'var(--os-ember)', color: '#fffdf8' } : { borderColor: 'var(--os-line)', color: 'var(--os-ink-soft)' }}>
          All
        </button>
        {integrations.filter((i) => i.enabled && i.provides_calendar).map((i) => {
          const meta = integrationMeta(i.type);
          const active = activeIntegration === i.type;
          return (
            <button key={i.type} onClick={() => setActiveIntegration(i.type)} className="flex items-center gap-2 rounded-full border px-4 py-1.5 text-xs font-bold transition" style={active ? { background: meta.color, borderColor: meta.color, color: textOn(meta.color) } : { borderColor: 'var(--os-line)', color: 'var(--os-ink-soft)' }}>
              <span className="h-2 w-2 rounded-full" style={{ background: meta.color }} />
              {meta.label}
            </button>
          );
        })}
      </div>

      {/* VIEWS */}
      {view === 'agenda' && (
        <div className="max-w-3xl">
          <div className="flex flex-col gap-2">
            {days.map((day) => {
              const key = ymd(day);
              const evs = byDay.get(key) ?? [];
              if (evs.length === 0) return null;
              const isToday = key === todayKey;
              return (
                <div key={key} className="flex gap-4 py-2">
                  <button onClick={() => openDay(key)} className={`flex h-20 w-20 shrink-0 flex-col items-center justify-center rounded-2xl ${isToday ? '' : ''}`} style={isToday ? { ...todayNum, boxShadow: 'var(--os-shadow)' } : { background: 'var(--os-paper-deep)', color: 'var(--os-ink)' }}>
                    <span className={`text-xs font-extrabold uppercase ${isToday ? 'text-white/80' : 'text-[#a89f8d]'}`}>{day.toLocaleDateString('en-US', { weekday: 'short' })}</span>
                    <span className="os-display text-3xl leading-none">{day.getDate()}</span>
                    <span className={`text-xs font-bold ${isToday ? 'text-white/80' : 'text-[#a89f8d]'}`}>{day.toLocaleDateString('en-US', { month: 'short' })}</span>
                  </button>
                  <div className="flex min-w-0 flex-1 flex-col gap-2">
                    {evs.map((ev, idx) => <EventCard key={`${ev.summary}-${idx}`} ev={ev} size="lg" />)}
                  </div>
                </div>
              );
            })}
            {visibleEvents.length === 0 && !isLoading && (
              <div className="mt-16 text-center os-display text-3xl" style={{ color: 'var(--os-ink-faint)' }}>Nothing coming up — enjoy the quiet</div>
            )}
          </div>
        </div>
      )}

      {view === 'day' && (
        <div className="mx-auto max-w-3xl">
          <div className="os-display text-5xl mb-1">{focused.toLocaleDateString('en-US', { weekday: 'long' })}</div>
          <div className="text-xl font-bold mb-4" style={{ color: 'var(--os-ink-soft)' }}>{focused.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}</div>
          <div className="flex flex-col gap-3">
            {(byDay.get(ymd(focused)) ?? []).map((ev, idx) => <EventCard key={`${ev.summary}-${idx}`} ev={ev} size="lg" />)}
            {(byDay.get(ymd(focused)) ?? []).length === 0 && (
              <div className="mt-12 flex flex-col items-center gap-4 text-center">
                <div className="os-display text-3xl" style={{ color: 'var(--os-ink-faint)' }}>A clear day</div>
                <button onClick={() => setShowSources(true)} className="rounded-full px-5 py-2.5 text-sm font-bold text-[#fffdf8]" style={{ background: 'var(--os-ember)' }}><Plus size={16} className="inline mr-1" />Add something</button>
              </div>
            )}
          </div>
        </div>
      )}

      {view === 'week' && (
        <div className="grid grid-cols-7 gap-3">
          {days.map((day) => {
            const key = ymd(day);
            const evs = byDay.get(key) ?? [];
            const isToday = key === todayKey;
            return (
              <div key={key} className={`flex min-h-0 flex-col rounded-[1.25rem] p-2 ${isToday ? '' : ''}`} style={isToday ? todayCell('1.25rem') : { background: 'var(--os-paper-deep)' }}>
                <button onClick={() => openDay(key)} className="mb-2 flex items-baseline gap-2 rounded-xl px-2 py-1 text-left">
                  <span className={`text-sm font-extrabold uppercase ${isToday ? 'text-[#bf4526]' : 'text-[#a89f8d]'}`} style={isToday ? { color: 'var(--os-ember-deep)' } : undefined}>{day.toLocaleDateString('en-US', { weekday: 'short' })}</span>
                  <span className={`os-display text-3xl ${isToday ? 'flex h-11 w-11 items-center justify-center rounded-full text-[#fffdf8]' : 'text-[#34302a]'}`} style={isToday ? todayNum : { color: 'var(--os-ink)' }}>{day.getDate()}</span>
                </button>
                <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                  {evs.map((ev, idx) => <EventCard key={`${ev.summary}-${idx}`} ev={ev} />)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {view === 'month' && (
        <div className="flex flex-col">
          <div className="grid grid-cols-7 gap-x-2 pb-1">
            {WEEKDAYS.map((w) => <div key={w} className="px-2 text-sm font-extrabold uppercase text-[#a89f8d]" style={{ color: 'var(--os-ink-faint)' }}>{w}</div>)}
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-7 grid-rows-6 gap-2">
            {days.map((day) => {
              const key = ymd(day);
              const evs = byDay.get(key) ?? [];
              const isToday = key === todayKey;
              const inMonth = day.getMonth() === focused.getMonth();
              const overflow = evs.length - 3;
              return (
                <button key={key} onClick={() => openDay(key)} className={`flex min-h-0 flex-col overflow-hidden rounded-2xl p-1.5 text-left ${isToday ? '' : ''}`} style={isToday ? todayCell('1rem') : inMonth ? { background: 'var(--os-card)', boxShadow: 'var(--os-shadow)' } : { background: 'var(--os-paper-deep)' }}>
                  <span className={`mb-1 flex h-8 w-8 items-center justify-center rounded-full os-display text-lg ${isToday ? 'text-[#fffdf8]' : inMonth ? 'text-[#34302a]' : 'text-[#a89f8d]'}`} style={isToday ? todayNum : { color: inMonth ? 'var(--os-ink)' : 'var(--os-ink-faint)' }}>{day.getDate()}</span>
                  <span className="flex min-h-0 flex-col gap-1 overflow-hidden">
                    {evs.slice(0, 3).map((ev, idx) => {
                      const meta = integrationMeta(ev.integration);
                      const ad = isAllDay(ev);
                      return (
                        <span key={idx} className="truncate rounded-md px-1.5 py-0.5 text-xs font-bold" style={ad ? { background: meta.color, color: textOn(meta.color) } : { background: 'transparent', color: 'var(--os-ink)', borderLeft: `3px solid ${meta.color}` }}>{ev.summary}</span>
                      );
                    })}
                    {overflow > 0 && <span className="px-1.5 text-xs font-extrabold text-[#a89f8d]" style={{ color: 'var(--os-ink-faint)' }}>+{overflow} more</span>}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {isLoading && <p className="text-sm" style={{ color: 'var(--os-ink-soft)' }}>Loading your agenda…</p>}
      {error && <p className="text-sm" style={{ color: 'var(--os-ember-deep)' }}>Failed to load calendar.</p>}

      {/* Add event */}
      <div className="mt-6 rounded-2xl border p-4" style={{ background: 'var(--os-panel-bg)', borderColor: 'var(--os-line)' }}>
        <p className="mb-3 text-xs font-black uppercase tracking-widest" style={{ color: 'var(--os-ink-faint)' }}>Add Event</p>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <input type="text" value={summary} onChange={(e) => setSummary(e.target.value)} className="rounded-xl border px-3 py-2 text-sm outline-none" style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }} placeholder="Event title" />
          <input type="text" value={startInput} onChange={(e) => setStartInput(e.target.value)} className="rounded-xl border px-3 py-2 text-sm outline-none" style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }} placeholder="When (e.g. tomorrow at 2pm)" />
          <div className="flex gap-2">
            <select aria-label="Target calendar" value={addIntegration} onChange={(e) => setAddIntegration(e.target.value)} className="rounded-xl border px-3 py-2 text-sm outline-none" style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }}>
              <option value="">Default ({integrationMeta(detail?.default).label})</option>
              {writable.map((i) => <option key={i.type} value={i.type}>{integrationMeta(i.type).label}</option>)}
            </select>
            <button onClick={() => { if (!summary.trim() || !startInput.trim()) { toast.error('Enter a title and time'); return; } addMutation.mutate(); }} className="shrink-0 rounded-xl px-4 py-2.5 text-[10px] font-black uppercase tracking-widest text-[#fffdf8]" style={{ background: 'var(--os-ember)' }}><Plus size={14} />Add</button>
          </div>
        </div>
      </div>

      {showSources && (
        <div className="mt-6 rounded-2xl border p-4 space-y-4" style={{ background: 'var(--os-panel-bg)', borderColor: 'var(--os-line)' }}>
          <div className="flex items-center justify-between">
            <p className="text-sm font-bold uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>Connected Sources</p>
            <button onClick={() => setShowSources(false)} className="p-1" style={{ color: 'var(--os-ink-soft)' }}><X size={16} /></button>
          </div>
          {integrations.map((i) => {
            const meta = integrationMeta(i.type);
            const isDisabled = disabledSet.has(i.type);
            return (
              <div key={i.type} className="flex items-center justify-between rounded-xl border p-3" style={{ background: 'var(--os-panel-bg)', borderColor: 'var(--os-line)' }}>
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
                  <div>
                    <p className="text-sm font-bold" style={{ color: 'var(--os-ink)' }}>{meta.label}</p>
                    <p className="text-[10px]" style={{ color: 'var(--os-ink-soft)' }}>{i.writable ? 'Read & write' : 'Read-only'}</p>
                  </div>
                </div>
                <button onClick={() => toggleDisabledMutation.mutate(i.type)} className={`flex items-center gap-1 rounded-full border px-3 py-1 text-[10px] font-bold transition`} style={isDisabled ? { borderColor: 'var(--os-line)', color: 'var(--os-ink-soft)' } : { borderColor: 'var(--os-line)', color: 'var(--os-ink-soft)' }}>
                  {isDisabled ? <Check size={12} /> : null}{isDisabled ? 'Enable' : 'Disable'}
                </button>
              </div>
            );
          })}
          <div className="pt-2 border-t" style={{ borderColor: 'var(--os-line)' }}>
            <p className="mb-2 text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>iCal Subscriptions</p>
            <div className="flex gap-2">
              <input type="text" value={icalUrl} onChange={(e) => setIcalUrl(e.target.value)} className="flex-1 rounded-xl border px-3 py-2 text-sm outline-none" style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }} placeholder="https://example.com/feed.ics" />
              <button onClick={() => { if (!icalUrl.trim()) { toast.error('Enter a .ics URL'); return; } addIcalMutation.mutate(icalUrl.trim()); }} className="rounded-xl px-4 py-2 text-[10px] font-black uppercase tracking-widest text-[#fffdf8]" style={{ background: 'var(--os-ember)' }}><Plus size={12} />Add</button>
            </div>
            {(settingsData?.settings?.ical_urls ?? []).map((u) => <p key={u} className="mt-2 truncate text-[10px]" style={{ color: 'var(--os-ink-soft)' }}>{u}</p>)}
          </div>
        </div>
      )}
    </section>
  );
};

export default CalendarApp;

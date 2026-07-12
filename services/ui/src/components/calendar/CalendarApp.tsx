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
  RefreshCw as RefreshIcon,
} from 'lucide-react';
import toast from 'react-hot-toast';
import Modal from '../../components/ui/Modal';
import { useDarkModeSync } from '../../hooks/useDarkModeSync';
import { api } from '../../services/api';
import { integrationMeta } from './integrationMeta';
import { calendarColor, calendarLabel, type CalendarPerson } from './calendarMeta';
import PeoplePanel from './PeoplePanel';
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
  available?: boolean;
  error?: string;
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
const EventCard = ({ ev, size = 'md', onSelect, people }: { ev: CalendarEvent; size?: 'md' | 'lg'; onSelect?: (ev: CalendarEvent) => void; people?: CalendarPerson[] }) => {
  const color = calendarColor(ev.calendar, people);
  const allDay = isAllDay(ev);
  if (allDay) {
    return (
      <button
        type="button"
        onClick={() => onSelect?.(ev)}
        className={`w-full text-left font-bold shadow-[0_1px_3px_rgba(72,60,38,0.07),0_10px_28px_-10px_rgba(72,60,38,0.16)] ${size === 'lg' ? 'min-h-14 rounded-2xl px-4 py-3 text-lg' : 'min-h-11 rounded-xl px-3 py-2 text-[15px]'}`}
        style={{ backgroundColor: color, color: textOn(color) }}
      >
        <span className="truncate">{ev.summary}</span>
        <span className="mt-0.5 block truncate text-[11px] font-semibold opacity-80">{calendarLabel(ev.calendar, people)}</span>
      </button>
    );
  }
  return (
      <button
        type="button"
        onClick={() => onSelect?.(ev)}
        className={`relative w-full text-left overflow-hidden shadow-[0_1px_3px_rgba(72,60,38,0.07),0_10px_28px_-10px_rgba(72,60,38,0.16)] ${size === 'lg' ? 'rounded-2xl py-4 pr-4 pl-5' : 'rounded-xl py-2 pr-3 pl-4'}`}
        style={{ backgroundColor: 'var(--os-card)' }}
        title={calendarLabel(ev.calendar, people)}
      >
      <span className="pointer-events-none absolute inset-y-0 left-0 w-1.5" style={{ backgroundColor: color }} aria-hidden="true" />
      <div className="flex items-center gap-1.5">
        <span className={`font-extrabold ${size === 'lg' ? 'text-base' : 'text-[13px]'}`} style={{ color }}>{formatTime(ev.start_time)}</span>
      </div>
      <div className={`truncate font-bold ${size === 'lg' ? 'text-xl' : 'text-[15px]'}`} style={{ color: 'var(--os-ink)' }}>{ev.summary}</div>
      <div className="text-[11px] font-semibold" style={{ color }}>{calendarLabel(ev.calendar, people)}</div>
      {size === 'lg' && ev.location && (
        <div className="mt-1 flex items-center gap-1 text-sm font-semibold" style={{ color: 'var(--os-ink-soft)' }}>
          <MapPin size={14} />
          <span className="truncate">{ev.location}</span>
        </div>
      )}
    </button>
  );
};

const EditEventModal = ({ event, onClose, people }: { event: CalendarEvent; onClose: () => void; people?: CalendarPerson[] }) => {
  const queryClient = useQueryClient();
  const meta = integrationMeta(event.integration);
  const isSkylight = (event.integration || '').toLowerCase() === 'skylight';
  const pad = (n: number) => String(n).padStart(2, '0');
  const d = new Date(event.start_time);
  const [summary, setSummary] = useState(event.summary);
  const [startInput, setStartInput] = useState(
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  );

  const updateMut = useMutation({
    mutationFn: () =>
      api.updateCalendarEvent({
        action: 'update',
        integration: event.integration,
        ...(isSkylight && event.id ? { event_id: event.id } : { query: event.summary }),
        summary: summary.trim(),
        start_time: startInput.trim(),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
      toast.success('Event updated');
      onClose();
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to update event'),
  });
  const deleteMut = useMutation({
    mutationFn: () =>
      api.deleteCalendarEvent({
        action: 'delete',
        integration: event.integration,
        ...(isSkylight && event.id ? { event_id: event.id } : { query: event.summary }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
      toast.success('Event deleted');
      onClose();
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to delete event'),
  });

  return (
    <Modal isOpen={true} onClose={onClose} title="Edit Event" size="md">
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--os-ink-soft)' }}>
          <span className="h-3 w-3 rounded-full" style={{ backgroundColor: calendarColor(event.calendar, people) }} />
          {meta.label}
          <span className="font-semibold" style={{ color: calendarColor(event.calendar, people) }}>· {calendarLabel(event.calendar, people)}</span>
        </div>
        <div>
          <label className="mb-1 block text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>Title</label>
          <input
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            className="w-full rounded-xl border px-3 py-2 text-sm outline-none"
            style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>When</label>
          <input
            value={startInput}
            onChange={(e) => setStartInput(e.target.value)}
            placeholder="YYYY-MM-DD HH:MM"
            className="w-full rounded-xl border px-3 py-2 text-sm outline-none"
            style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }}
          />
          <p className="mt-1 text-[11px]" style={{ color: 'var(--os-ink-faint)' }}>24-hour date/time. Leave as-is to keep the current time.</p>
        </div>
        {event.location && (
          <p className="flex items-center gap-1 text-sm" style={{ color: 'var(--os-ink-soft)' }}>
            <MapPin size={13} /> {event.location}
          </p>
        )}
        <div className="flex items-center justify-between gap-2 pt-2">
          <button
            onClick={() => { if (window.confirm('Delete this event?')) deleteMut.mutate(); }}
            className="rounded-xl px-4 py-2 text-xs font-black uppercase tracking-widest text-[#fffdf8]"
            style={{ background: '#dc2626' }}
          >
            Delete
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="rounded-xl border px-4 py-2 text-xs font-bold"
              style={{ borderColor: 'var(--os-line)', color: 'var(--os-ink-soft)' }}
            >
              Cancel
            </button>
            <button
              disabled={!summary.trim()}
              onClick={() => updateMut.mutate()}
              className="rounded-xl px-4 py-2 text-xs font-black uppercase tracking-widest text-[#fffdf8]"
              style={{ background: 'var(--os-ember)' }}
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </Modal>
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
  const [editing, setEditing] = useState<CalendarEvent | null>(null);
  const { data, isLoading, error, refetch } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-app'],
    queryFn: () => api.getCalendarEvents(),
    refetchInterval: (q) => {
      const integ = (q.state.data?.detail as CalendarDetail | undefined)?.integrations ?? [];
      const down = integ.filter((i) => i.enabled && i.available === false);
      return down.length > 0 ? 30_000 : 300_000;
    },
  });
  const { data: settingsData } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-settings'],
    queryFn: () => api.getCalendarSettings(),
  });

  const detail = data?.detail as CalendarDetail | undefined;
  const integrations = detail?.integrations ?? [];
  const unavailable = integrations.filter((i) => i.enabled && i.available === false);
  const allEvents = useMemo<CalendarEvent[]>(
    () => (data?.events as CalendarEvent[] | undefined) ?? [],
    [data]
  );
  const visibleEvents = useMemo(
    () => (activeIntegration === 'all' ? allEvents : allEvents.filter((e) => (e.integration ?? '') === activeIntegration)),
    [allEvents, activeIntegration]
  );

  const settings = (settingsData?.settings ?? {}) as { people?: CalendarPerson[] };
  const people = settings.people ?? [];
  const discovered = useMemo(
    () => [...new Set(allEvents.map((e) => e.calendar).filter(Boolean) as string[])],
    [allEvents]
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

  const savePeopleMutation = useMutation({
    mutationFn: (next: CalendarPerson[]) => api.updateCalendarSettings({ people: next }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['calendar-settings'] }); queryClient.invalidateQueries({ queryKey: ['calendar-app'] }); toast.success('People saved'); },
    onError: (e: Error) => toast.error(e.message || 'Failed to save people'),
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
.os-calendar{
${isDark ? DARK_VARS : LIGHT_VARS}
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
        <button onClick={() => refetch()} title="Refresh" className="rounded-full p-2" style={{ background: 'var(--os-paper-deep)', color: 'var(--os-ink-soft)' }}>
          <RefreshIcon size={14} />
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
                    {evs.map((ev, idx) => <EventCard key={`${ev.summary}-${idx}`} ev={ev} size="lg" onSelect={setEditing} people={people} />)}
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
            {(byDay.get(ymd(focused)) ?? []).map((ev, idx) => <EventCard key={`${ev.summary}-${idx}`} ev={ev} size="lg" onSelect={setEditing} people={people} />)}
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
                  {evs.map((ev, idx) => <EventCard key={`${ev.summary}-${idx}`} ev={ev} onSelect={setEditing} people={people} />)}
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
                      const ad = isAllDay(ev);
                      const c = calendarColor(ev.calendar, people);
                      return (
                        <span key={idx} role="button" tabIndex={0} onClick={() => setEditing(ev)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEditing(ev); } }} title={calendarLabel(ev.calendar, people)} className={`relative cursor-pointer truncate overflow-hidden rounded-md py-0.5 pl-2.5 pr-1.5 text-xs font-bold ${ad ? '' : ''}`} style={ad ? { background: c, color: textOn(c) } : { background: 'transparent', color: 'var(--os-ink)' }}>
                          {!ad && <span className="pointer-events-none absolute inset-y-0 left-0 w-1" style={{ backgroundColor: c }} aria-hidden="true" />}
                          <span className="truncate">{ev.summary}</span>
                        </span>
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
      {unavailable.length > 0 && (
        <div className="my-3 flex items-center gap-2 rounded-xl border px-3 py-2 text-xs" style={{ background: 'var(--os-sun-soft)', borderColor: 'var(--os-sun)', color: 'var(--os-ink)' }}>
          <RefreshIcon size={14} className="animate-spin" />
          {unavailable.map((i) => integrationMeta(i.type).label).join(', ')} temporarily unreachable — showing cached events and retrying.
        </div>
      )}

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
            const isDown = i.available === false;
            return (
              <div key={i.type} className="flex items-center justify-between rounded-xl border p-3" style={{ background: 'var(--os-panel-bg)', borderColor: 'var(--os-line)', opacity: isDown && !isDisabled ? 0.6 : 1 }}>
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: isDown ? 'var(--os-ink-faint)' : meta.color }} />
                  <div>
                    <p className="text-sm font-bold" style={{ color: 'var(--os-ink)' }}>{meta.label}{isDown ? ' — temporarily unreachable' : ''}</p>
                    <p className="text-[10px]" style={{ color: 'var(--os-ink-soft)' }}>{isDisabled ? 'Disabled' : i.writable ? 'Read & write' : 'Read-only'}{isDown ? ' · retrying' : ''}</p>
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
          <div className="border-t pt-4" style={{ borderColor: 'var(--os-line)' }}>
            <p className="mb-3 text-xs font-black uppercase tracking-widest" style={{ color: 'var(--os-ink-faint)' }}>People</p>
            <PeoplePanel key={people.map((p) => p.id).join('|')} people={people} discovered={discovered} onSave={(p) => savePeopleMutation.mutate(p)} />
          </div>
        </div>
      )}
      {editing && <EditEventModal event={editing} onClose={() => setEditing(null)} people={people} />}
    </section>
  );
};

export default CalendarApp;

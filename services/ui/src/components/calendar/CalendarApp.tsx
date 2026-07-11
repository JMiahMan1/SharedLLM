import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Calendar as CalendarIcon, Plus, Settings as SettingsIcon, Check, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { integrationMeta } from './integrationMeta';
import type { CalendarEvent } from '../../types/widget';
import type { ExecutionResponse } from '../../services/api';

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

const formatTime = (iso: string): string => {
  const d = new Date(iso);
  let h = d.getHours();
  const m = d.getMinutes();
  const period = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${m.toString().padStart(2, '0')} ${period}`;
};

const formatDayHeader = (iso: string): string => {
  const d = new Date(iso);
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (d.toDateString() === now.toDateString()) return 'Today';
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
};

const CalendarApp = () => {
  const queryClient = useQueryClient();
  const [activeIntegration, setActiveIntegration] = useState<string>('all');
  const [showSettings, setShowSettings] = useState(false);
  const [summary, setSummary] = useState('');
  const [startInput, setStartInput] = useState('');
  const [addIntegration, setAddIntegration] = useState('');

  const { data, isLoading, error } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-app'],
    queryFn: () => api.getCalendarEvents(),
    refetchInterval: 300000,
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

  const visibleEvents = useMemo(() => {
    if (activeIntegration === 'all') return allEvents;
    return allEvents.filter((e) => (e.integration ?? '') === activeIntegration);
  }, [allEvents, activeIntegration]);

  const grouped = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const ev of visibleEvents) {
      const key = ev.start_time ? new Date(ev.start_time).toDateString() : 'Unknown';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(ev);
    }
    return Array.from(map.entries()).sort((a, b) =>
      new Date(a[0]).getTime() - new Date(b[0]).getTime()
    );
  }, [visibleEvents]);

  const disabledSet = new Set(settingsData?.settings?.disabled ?? []);
  const writableIntegrations = integrations.filter((i) => i.enabled && i.writable);

  const addMutation = useMutation({
    mutationFn: () =>
      api.addCalendarEvent({
        summary: summary.trim(),
        start_time: startInput.trim(),
        integration: addIntegration || detail?.default || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
      toast.success('Event added');
      setSummary('');
      setStartInput('');
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to add event'),
  });

  const setDefaultMutation = useMutation({
    mutationFn: (value: string) => api.updateCalendarSettings({ default: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
      toast.success('Default calendar updated');
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to update default'),
  });

  const toggleDisabledMutation = useMutation({
    mutationFn: (type: string) => {
      const next = new Set(disabledSet);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return api.updateCalendarSettings({ disabled: Array.from(next) });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-settings'] });
      queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to update integration'),
  });

  const [icalUrl, setIcalUrl] = useState('');
  const addIcalMutation = useMutation({
    mutationFn: (url: string) => {
      const current = settingsData?.settings?.ical_urls ?? [];
      return api.updateCalendarSettings({ ical_urls: [...current, url] });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-settings'] });
      queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
      setIcalUrl('');
      toast.success('iCal feed added');
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to add iCal feed'),
  });

  return (
    <section className="glass-panel p-6">
      <div className="mb-6 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <CalendarIcon size={20} className="text-emerald-300" />
          <div>
            <h3 className="text-xl font-bold text-white">Calendar</h3>
            <p className="text-sm text-slate-400">Merged family agenda across every connected source.</p>
          </div>
        </div>
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="glass-button px-3 py-2 text-[10px] font-black uppercase tracking-widest"
        >
          <SettingsIcon size={14} className="inline mr-1" />
          Sources
        </button>
      </div>

      {detail?.needs_default_choice && (
        <div className="mb-5 rounded-2xl border border-amber-400/30 bg-amber-500/10 p-4">
          <p className="text-sm font-semibold text-amber-200">Choose a default calendar</p>
          <p className="text-xs text-amber-300/80 mb-3">
            You have more than one calendar source connected. Pick which one opens by default.
          </p>
          <div className="flex flex-wrap gap-2">
            {(detail.available_defaults ?? integrations.filter((i) => i.enabled).map((i) => i.type)).map((t) => (
              <button
                key={t}
                onClick={() => setDefaultMutation.mutate(t)}
                className="rounded-full border border-amber-400/40 bg-amber-500/10 px-3 py-1.5 text-xs font-bold text-amber-200 hover:bg-amber-500/20 transition"
              >
                {integrationMeta(t).label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mb-5 flex flex-wrap gap-2">
        <button
          onClick={() => setActiveIntegration('all')}
          className={`rounded-full border px-4 py-1.5 text-xs font-bold transition ${
            activeIntegration === 'all'
              ? 'border-white/40 bg-white/10 text-white'
              : 'border-white/10 bg-white/5 text-slate-400 hover:text-white'
          }`}
        >
          All
        </button>
        {integrations
          .filter((i) => i.enabled && i.provides_calendar)
          .map((i) => {
            const meta = integrationMeta(i.type);
            const active = activeIntegration === i.type;
            return (
              <button
                key={i.type}
                onClick={() => setActiveIntegration(i.type)}
                className={`flex items-center gap-2 rounded-full border px-4 py-1.5 text-xs font-bold transition ${meta.chip} ${
                  active ? 'ring-2 ring-white/30' : ''
                }`}
              >
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: meta.color }} />
                {meta.label}
              </button>
            );
          })}
      </div>

      <div className="space-y-6">
        {grouped.length === 0 && !isLoading && (
          <div className="flex flex-col items-center justify-center text-center py-16 rounded-2xl border border-dashed border-slate-800 bg-slate-900/10">
            <span className="text-4xl mb-3">📅</span>
            <p className="text-sm text-slate-400">No upcoming events</p>
            <p className="text-xs text-slate-500">Your agenda is fully clear</p>
          </div>
        )}
        {isLoading && <p className="text-sm text-slate-500">Loading your agenda…</p>}
        {error && <p className="text-sm text-red-400">Failed to load calendar.</p>}
        {grouped.map(([day, evs]) => (
          <div key={day}>
            <p className="mb-3 text-xs font-black uppercase tracking-widest text-slate-500">{formatDayHeader(evs[0]?.start_time ?? day)}</p>
            <div className="space-y-3">
              {evs.map((ev, idx) => {
                const meta = integrationMeta(ev.integration);
                return (
                  <div
                    key={`${ev.summary}-${idx}`}
                    className="flex items-start gap-4 rounded-2xl border border-white/5 bg-slate-900/50 p-4 hover:border-slate-700/50 transition"
                  >
                    <div className="flex min-w-[4.5rem] flex-col items-center rounded-xl border border-white/5 bg-slate-950/40 p-2">
                      <span className="text-sm font-bold text-emerald-300">{formatTime(ev.start_time)}</span>
                      {ev.end_time && (
                        <span className="mt-0.5 text-[10px] text-slate-500">{formatTime(ev.end_time)}</span>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: meta.color }} title={meta.label} />
                        <p className="truncate text-sm font-semibold text-white">{ev.summary}</p>
                      </div>
                      {ev.location && (
                        <p className="mt-1 flex items-center gap-1 text-xs text-slate-500">
                          <span>📍</span>
                          <span className="truncate">{ev.location}</span>
                        </p>
                      )}
                      {ev.calendar && (
                        <p className="mt-0.5 text-[10px] text-slate-600 truncate">{ev.calendar}</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-6 rounded-2xl border border-white/5 bg-black/20 p-4">
        <p className="mb-3 text-xs font-black uppercase tracking-widest text-slate-500">Add Event</p>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <input
            type="text"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            className="glass-input"
            placeholder="Event title"
          />
          <input
            type="text"
            value={startInput}
            onChange={(e) => setStartInput(e.target.value)}
            className="glass-input"
            placeholder="When (e.g. tomorrow at 2pm)"
          />
          <div className="flex gap-2">
            <select
              aria-label="Target calendar"
              value={addIntegration}
              onChange={(e) => setAddIntegration(e.target.value)}
              className="glass-input bg-black/30"
            >
              <option value="">Default ({integrationMeta(detail?.default).label})</option>
              {writableIntegrations.map((i) => (
                <option key={i.type} value={i.type}>
                  {integrationMeta(i.type).label}
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                if (!summary.trim() || !startInput.trim()) {
                  toast.error('Enter a title and time');
                  return;
                }
                addMutation.mutate();
              }}
              className="glass-button shrink-0 px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <Plus size={14} />
              Add
            </button>
          </div>
        </div>
      </div>

      {showSettings && (
        <div className="mt-6 rounded-2xl border border-white/10 bg-white/5 p-4 space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-bold uppercase tracking-wider text-slate-300">Connected Sources</p>
            <button onClick={() => setShowSettings(false)} className="p-1 text-slate-400 hover:text-white">
              <X size={16} />
            </button>
          </div>
          {integrations.map((i) => {
            const meta = integrationMeta(i.type);
            const isDisabled = disabledSet.has(i.type);
            return (
              <div key={i.type} className="flex items-center justify-between rounded-xl border border-white/5 bg-black/20 p-3">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: meta.color }} />
                  <div>
                    <p className="text-sm font-semibold text-white">{meta.label}</p>
                    <p className="text-[10px] text-slate-500">{i.writable ? 'Read & write' : 'Read-only'}</p>
                  </div>
                </div>
                <button
                  onClick={() => toggleDisabledMutation.mutate(i.type)}
                  className={`flex items-center gap-1 rounded-full border px-3 py-1 text-[10px] font-bold ${
                    isDisabled
                      ? 'border-emerald-400/40 bg-emerald-500/10 text-emerald-300'
                      : 'border-slate-400/30 bg-slate-500/10 text-slate-400'
                  }`}
                >
                  {isDisabled ? <Check size={12} /> : null}
                  {isDisabled ? 'Enable' : 'Disable'}
                </button>
              </div>
            );
          })}

          <div className="pt-2 border-t border-white/5">
            <p className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-300">iCal Subscriptions</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={icalUrl}
                onChange={(e) => setIcalUrl(e.target.value)}
                className="glass-input flex-1"
                placeholder="https://example.com/feed.ics"
              />
              <button
                onClick={() => {
                  if (!icalUrl.trim()) {
                    toast.error('Enter a .ics URL');
                    return;
                  }
                  addIcalMutation.mutate(icalUrl.trim());
                }}
                className="glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest"
              >
                <Plus size={12} /> Add
              </button>
            </div>
            {(settingsData?.settings?.ical_urls ?? []).map((u) => (
              <p key={u} className="mt-2 truncate text-[10px] text-slate-500">{u}</p>
            ))}
          </div>
        </div>
      )}
    </section>
  );
};

export default CalendarApp;

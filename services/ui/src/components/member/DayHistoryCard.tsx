import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Car, ChevronLeft, ChevronRight, History, MapPin } from 'lucide-react';
import { api } from '../../services/api';
import MemberCard from './MemberCard';
import { localDay } from '../../lib/healthHub';
import { getMapStyle, resolveMapScheme, subscribeMapStyle } from '../../lib/mapStyle';
import type { DayStop, DayTimelineEntry, PersonDay, TripLocation } from '../../types/api';

/** How far back the history goes: geo keeps breadcrumbs this long. */
export const HISTORY_DAYS = 30;

const clock = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const placeName = (l: TripLocation | undefined | null) => l?.zone || l?.name || l?.address || 'somewhere';

function duration(seconds: number): string {
  const mins = Math.max(1, Math.round(seconds / 60));
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function shiftDay(day: string, by: number): string {
  const [y, m, d] = day.split('-').map(Number);
  return localDay(new Date(y, m - 1, d + by));
}

function dayLabel(day: string, today: string): string {
  if (day === today) return 'Today';
  if (day === shiftDay(today, -1)) return 'Yesterday';
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

/** The day's path with its stops numbered, on the same light/dark map as Wander. */
function DayMap({ data }: { data: PersonDay }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!ref.current || data.points.length === 0) return undefined;
    const container = ref.current;
    const map = L.map(container, { zoomControl: true, attributionControl: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    const line = L.polyline(data.points.map((p) => [p.lat, p.lon] as L.LatLngTuple), { color: '#22d3ee', weight: 3, opacity: 0.85 }).addTo(map);
    data.stops.forEach((stop: DayStop, i) => {
      L.marker([stop.lat, stop.lon], {
        icon: L.divIcon({
          className: '',
          html: `<span style="display:flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:9999px;background:#0891b2;border:2px solid #fff;color:#fff;font:700 11px/1 ui-sans-serif,system-ui,sans-serif;box-shadow:0 2px 6px rgba(0,0,0,.4)">${i + 1}</span>`,
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        }),
        title: stop.place || `Stop ${i + 1}`,
      }).addTo(map);
    });
    map.fitBounds(line.getBounds(), { padding: [20, 20], maxZoom: 16 });
    const scheme = () => {
      const c = map.getCenter();
      container.classList.toggle('jarvis-map-dark', resolveMapScheme(getMapStyle(), Date.now(), { lat: c.lat, lon: c.lng }) === 'dark');
    };
    scheme();
    const stop = subscribeMapStyle(scheme);
    return () => {
      stop();
      map.remove();
    };
  }, [data]);
  if (data.points.length === 0) return null;
  return <div ref={ref} className="relative z-0 w-full rounded-xl" style={{ height: 260 }} data-testid="day-history-map" />;
}

function Entry({ entry, index }: { entry: DayTimelineEntry; index: number }) {
  if (entry.kind === 'stop') {
    return (
      <li className="flex gap-2.5 rounded-xl border border-white/10 bg-white/5 p-2.5 text-xs" data-testid="day-history-stop">
        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-cyan-600 text-[10px] font-bold text-white">{index}</span>
        <span className="min-w-0">
          <span className="block break-words text-slate-200">{entry.place || 'A place with no name'}</span>
          <span className="block text-[10px] text-slate-500">
            {clock(entry.since)} – {clock(entry.until)} · {duration(entry.until - entry.since)}
          </span>
        </span>
      </li>
    );
  }
  return (
    <li className="flex gap-2.5 rounded-xl border border-white/10 bg-white/5 p-2.5 text-xs" data-testid="day-history-trip">
      <Car size={16} className="mt-0.5 shrink-0 text-emerald-300" />
      <span className="min-w-0">
        <span className="block break-words text-slate-200">
          {placeName(entry.start_location)} to {placeName(entry.end_location)}
        </span>
        <span className="block text-[10px] text-slate-500">
          {clock(entry.start_time)}
          {entry.end_time ? ` – ${clock(entry.end_time)}` : ''} · {(entry.distance_miles ?? 0).toFixed(1)} mi
          {entry.duration_seconds ? ` · ${duration(entry.duration_seconds)}` : ''}
          {entry.top_speed_mph ? ` · top ${Math.round(entry.top_speed_mph)} mph` : ''}
        </span>
      </span>
    </li>
  );
}

/**
 * Where someone went on a given day, for the last 30 days: the path on a map,
 * the places they stayed (numbered), and their trips, in order (Life360's
 * location history). Their sharing consent applies; geo refuses otherwise.
 */
export default function DayHistoryCard({ username, name }: { username: string; name: string }) {
  const today = localDay();
  const [day, setDay] = useState(today);
  const oldest = shiftDay(today, -(HISTORY_DAYS - 1));
  const { data, isLoading, error } = useQuery({
    queryKey: ['person-day', username, day],
    queryFn: () => api.getPersonDay(username, day),
    retry: false,
    staleTime: day === today ? 60_000 : 30 * 60_000,
  });
  const refused = isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404);
  let stopNumber = 0;
  const picker = (
    <span className="flex items-center gap-1">
      <button
        type="button"
        aria-label="Previous day"
        disabled={day <= oldest}
        onClick={() => setDay(shiftDay(day, -1))}
        className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10 disabled:opacity-30"
      >
        <ChevronLeft size={16} />
      </button>
      <span className="min-w-[5.5rem] text-center text-xs font-medium text-slate-200" data-testid="day-history-day">{dayLabel(day, today)}</span>
      <button
        type="button"
        aria-label="Next day"
        disabled={day >= today}
        onClick={() => setDay(shiftDay(day, 1))}
        className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10 disabled:opacity-30"
      >
        <ChevronRight size={16} />
      </button>
    </span>
  );
  const nothing = data && data.points.length === 0 && data.timeline.length === 0;
  return (
    <MemberCard
      title="Location history"
      icon={History}
      tone="text-cyan-300"
      testId="member-day-history"
      wide
      aside={picker}
      isLoading={isLoading}
      error={refused ? `${name}'s location is not shared with you.` : error ? 'That day could not be loaded.' : null}
      empty={nothing ? `No location recorded for ${name} ${day === today ? 'yet today' : 'that day'}.` : null}
    >
      {data && (
        <div className="space-y-3">
          <DayMap data={data} />
          {data.timeline.length === 0 ? (
            <p className="flex items-center gap-1.5 text-xs text-slate-400">
              <MapPin size={13} /> Moving about, with no stop long enough to list.
            </p>
          ) : (
            <ol className="space-y-2">
              {data.timeline.map((entry) => (
                <Entry key={`${entry.kind}-${entry.at}`} entry={entry} index={entry.kind === 'stop' ? ++stopNumber : 0} />
              ))}
            </ol>
          )}
        </div>
      )}
    </MemberCard>
  );
}

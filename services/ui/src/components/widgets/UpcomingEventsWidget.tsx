import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { CalendarEvent } from '../../types/widget';

interface ParsedEvent {
  summary: string;
  start_time: string;
  end_time?: string;
  location?: string;
  startHour: number;
  startMinute: number;
  isToday: boolean;
  isVerySoon: boolean;
}

const parseEvent = (event: CalendarEvent): ParsedEvent => {
  const start = new Date(event.start_time);
  const now = new Date();
  const isToday = start.toDateString() === now.toDateString();
  const isVerySoon = isToday && start.getTime() - now.getTime() < 3600000;
  return {
    summary: event.summary,
    start_time: event.start_time,
    end_time: event.end_time,
    location: event.location,
    startHour: start.getHours(),
    startMinute: start.getMinutes(),
    isToday,
    isVerySoon,
  };
};

const parseStringEvent = (line: string): ParsedEvent | null => {
  const match = line.match(/^-\s+\[([^\]]+)\]\s+(.*?)(?:\s+\(([^)]+)\))?$/);
  if (!match) return null;
  const [, dateTimeStr, summary, location] = match;

  let start = new Date(dateTimeStr);
  if (isNaN(start.getTime())) {
    const parts = dateTimeStr.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s+(AM|PM)$/i);
    if (parts) {
      const [, y, m, d, hh, mm, period] = parts;
      let hour = parseInt(hh, 10);
      if (period.toUpperCase() === 'PM' && hour < 12) hour += 12;
      if (period.toUpperCase() === 'AM' && hour === 12) hour = 0;
      start = new Date(parseInt(y, 10), parseInt(m, 10) - 1, parseInt(d, 10), hour, parseInt(mm, 10));
    }
  }

  if (isNaN(start.getTime())) {
    return null;
  }

  const now = new Date();
  const isToday = start.toDateString() === now.toDateString();
  const isVerySoon = isToday && start.getTime() - now.getTime() < 3600000 && start.getTime() > now.getTime();

  return {
    summary,
    start_time: start.toISOString(),
    location: location || undefined,
    startHour: start.getHours(),
    startMinute: start.getMinutes(),
    isToday,
    isVerySoon,
  };
};

const formatTime = (hour: number, minute: number): string => {
  const period = hour >= 12 ? 'PM' : 'AM';
  const displayHour = hour % 12 || 12;
  const displayMinute = minute.toString().padStart(2, '0');
  return `${displayHour}:${displayMinute} ${period}`;
};

const formatRelativeTime = (date: Date): string => {
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffHours < 0) return 'Past';
  if (diffHours < 1) return 'In minutes';
  if (diffHours === 1) return 'In 1 hour';
  if (diffDays === 0) return `In ${diffHours} hours`;
  if (diffDays === 1) return 'Tomorrow';
  if (diffDays < 7) return `In ${diffDays} days`;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

const UpcomingEventsWidget = () => {
  const [events, setEvents] = useState<ParsedEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      const result = await api.getCalendarEvents() as { status: string; message?: string; events?: CalendarEvent[] };
      if (cancelled) return;

      if (result.status === 'SUCCESS') {
        let parsed: ParsedEvent[] = [];
        if (result.events) {
          parsed = result.events.map(parseEvent);
        } else if (typeof result.message === 'string') {
          const lines = result.message.split('\n');
          parsed = lines
            .map(parseStringEvent)
            .filter((e): e is ParsedEvent => e !== null);
        }

        const filteredAndSorted = parsed
          .filter((e) => new Date(e.start_time).getTime() > Date.now() - 3600000)
          .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime())
          .slice(0, 8);

        setEvents(filteredAndSorted);
        setLoading(false);
        setError(null);
      } else {
        setError(result.message || 'Failed to fetch events');
        setEvents([]);
        setLoading(false);
      }
    };

    load();
    const interval = setInterval(load, 300000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (loading) {
    return (
      <div className="glass-card h-full p-5 relative flex items-center justify-center">
        <p className="text-sm text-slate-500 animate-pulse">Loading events...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="glass-card h-full p-5 relative flex items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-red-400 mb-2">{error}</p>
          <p className="text-xs text-slate-500">Calendar may not be configured</p>
        </div>
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className="glass-card h-full p-5 relative flex items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-slate-400">No upcoming events</p>
          <p className="text-xs text-slate-500">Your schedule is clear</p>
        </div>
      </div>
    );
  }

  return (
    <div className="glass-card h-full p-5 relative">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-bold text-white text-lg">Upcoming</h3>
        <span className="text-xs text-slate-400">{events.length} events</span>
      </div>

      <div className="space-y-3 max-h-64 overflow-y-auto pr-1">
        {events.map((event, index) => {
          const relativeTime = formatRelativeTime(new Date(event.start_time));
          const isVerySoon = event.isVerySoon;

          return (
            <div
              key={`${event.summary}-${index}`}
              className={`p-3 rounded-lg border transition-all ${
                isVerySoon
                  ? 'bg-amber-500/10 border-amber-500/30'
                  : 'bg-slate-800/50 border-slate-700/50 hover:border-slate-600/50'
              }`}
            >
              <div className="flex items-start gap-3">
                <div className="flex flex-col items-center min-w-[3rem]">
                  <span className={`text-sm font-bold ${isVerySoon ? 'text-amber-400' : 'text-white'}`}>
                    {formatTime(event.startHour, event.startMinute)}
                  </span>
                  <span className={`text-xs ${isVerySoon ? 'text-amber-500' : 'text-slate-500'}`}>
                    {relativeTime}
                  </span>
                </div>

                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-medium truncate ${isVerySoon ? 'text-amber-300' : 'text-white'}`}>
                    {event.summary}
                  </p>
                  {event.location && (
                    <p className="text-xs text-slate-500 truncate">{event.location}</p>
                  )}
                </div>

                {isVerySoon && (
                  <span className="text-xs bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded-full shrink-0">
                    Soon
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default UpcomingEventsWidget;

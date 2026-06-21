import { useWidgetData } from '../../hooks/useWidgetData';
import { WidgetCard } from './WidgetCard';
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

interface UpcomingEventsWidgetProps {
  settingsButton?: React.ReactNode;
}

const UpcomingEventsWidget = ({ settingsButton }: UpcomingEventsWidgetProps) => {
  const fetchEvents = async () => {
    const result = await api.getCalendarEvents() as { status: string; message?: string; events?: CalendarEvent[] };
    if (result.status !== 'SUCCESS') {
      throw new Error(result.message || 'Failed to fetch events');
    }

    let parsed: ParsedEvent[] = [];
    if (result.events) {
      parsed = result.events.map(parseEvent);
    } else if (typeof result.message === 'string') {
      const lines = result.message.split('\n');
      parsed = lines
        .map(parseStringEvent)
        .filter((e): e is ParsedEvent => e !== null);
    }

    return parsed
      .filter((e) => new Date(e.start_time).getTime() > Date.now() - 3600000)
      .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime())
      .slice(0, 8);
  };

  const { data: events = [], isLoading, error, refetch } = useWidgetData<ParsedEvent[]>(
    ['calendar-events'],
    fetchEvents,
    300000 // 5 minutes
  );

  const eventList = (isExpanded: boolean) => (
    <div className={isExpanded ? "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" : "space-y-3 max-h-64 overflow-y-auto pr-1"}>
      {events.map((event, index) => {
        const relativeTime = formatRelativeTime(new Date(event.start_time));
        const isVerySoon = event.isVerySoon;

        return (
          <div
            key={`${event.summary}-${index}`}
            className={`p-4 rounded-xl border transition-all ${
              isVerySoon
                ? 'bg-amber-500/10 border-amber-500/30 shadow-lg shadow-amber-500/5'
                : 'bg-slate-900/50 border-slate-800 hover:border-slate-700/50'
            }`}
          >
            <div className="flex items-start gap-4">
              <div className="flex flex-col items-center min-w-[3.5rem] bg-slate-950/40 p-2 rounded-lg border border-white/5">
                <span className={`text-sm font-bold ${isVerySoon ? 'text-amber-400' : 'text-purple-400'}`}>
                  {formatTime(event.startHour, event.startMinute)}
                </span>
                <span className={`text-[10px] mt-0.5 ${isVerySoon ? 'text-amber-500/80' : 'text-slate-500'}`}>
                  {relativeTime}
                </span>
              </div>

              <div className="flex-1 min-w-0">
                <p className={`text-sm font-semibold truncate ${isVerySoon ? 'text-amber-300' : 'text-white'}`}>
                  {event.summary}
                </p>
                {event.location && (
                  <p className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                    <span>📍</span> <span className="truncate">{event.location}</span>
                  </p>
                )}
                {event.end_time && (
                  <p className="text-[10px] text-slate-600 mt-1">
                    Ends: {new Date(event.end_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                  </p>
                )}
              </div>

              {isVerySoon && (
                <span className="text-[10px] bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded-full shrink-0 font-bold">
                  Soon
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );

  return (
    <WidgetCard
      title="Upcoming Events"
      isLoading={isLoading}
      error={error}
      onRetry={refetch}
      settingsButton={settingsButton}
      isExpandable={true}
      icon="📅"
      actions={<span className="text-xs text-slate-400">{events.length} events</span>}
      expandedChildren={
        <div className="space-y-6 py-2">
          <div className="bg-slate-900/30 border border-slate-800 rounded-xl p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-white">Calendar Details</p>
              <p className="text-xs text-slate-500">View and manage your upcoming schedule. Connected via local system services.</p>
            </div>
            <button onClick={() => refetch()} className="glass-button px-4 py-2 text-xs font-bold text-indigo-400 self-start md:self-auto">
              Sync Calendar
            </button>
          </div>
          {events.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-20 bg-slate-900/10 rounded-2xl border border-dashed border-slate-800">
              <span className="text-4xl mb-4">📅</span>
              <p className="text-sm text-slate-400">No upcoming events found</p>
              <p className="text-xs text-slate-500">Your agenda is fully clear</p>
            </div>
          ) : (
            eventList(true)
          )}
        </div>
      }
    >
      {events.length === 0 ? (
        <div className="flex flex-col items-center justify-center text-center h-full">
          <p className="text-sm text-slate-400">No upcoming events</p>
          <p className="text-xs text-slate-500">Your schedule is clear</p>
        </div>
      ) : (
        eventList(false)
      )}
    </WidgetCard>
  );
};

export default UpcomingEventsWidget;

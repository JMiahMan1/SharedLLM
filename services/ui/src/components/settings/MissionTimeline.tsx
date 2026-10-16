import { eventColor, eventTime, eventType, logText, type MissionLogEvent } from '../../lib/missionLog';

interface MissionTimelineProps {
  events: MissionLogEvent[];
  /** Show tool-call payloads (the audit view); the summary view hides them. */
  showPayloads?: boolean;
  /** Only the most recent events are drawn so very long runs stay responsive. */
  limit?: number;
  emptyText?: string;
}

export function MissionTimeline({ events, showPayloads = false, limit = 500, emptyText = 'No execution events recorded.' }: MissionTimelineProps) {
  const shown = events.filter((e) => showPayloads || eventType(e) !== 'action_payload').slice(-limit);
  if (!shown.length) return <p className="text-slate-500 text-xs italic">{emptyText}</p>;
  return (
    <div className="space-y-1.5 font-mono text-[11px]">
      {shown.map((e, i) => {
        const type = eventType(e);
        const time = eventTime(e);
        return (
          <div key={i} className={`whitespace-pre-wrap break-words ${eventColor(type)}`}>
            {time && <span className="opacity-50 select-none mr-2">[{time}]</span>}
            {type === 'action_payload' ? (
              <span className="block pl-4 border-l-2 border-yellow-500/40">{logText(e.data)}</span>
            ) : (
              <span className={type === 'reasoning' ? 'italic opacity-80' : undefined}>{logText(e.data)}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

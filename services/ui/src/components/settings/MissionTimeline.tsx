import { eventColor, summarizeMissionEvents, type MissionEventKind, type MissionEventStatus, type MissionLogEvent } from '../../lib/missionLog';

interface MissionTimelineProps {
  events: MissionLogEvent[];
  /** Show tool-call payloads (the audit view); the summary view hides them. */
  showPayloads?: boolean;
  /** Only the most recent events are drawn so very long runs stay responsive. */
  limit?: number;
  emptyText?: string;
}

const KIND_TONE: Record<MissionEventKind, string> = {
  tool: 'border-white/10 bg-white/[0.03]',
  delegation: 'border-indigo-500/30 bg-indigo-500/10',
  subagent: 'border-cyan-500/30 bg-cyan-500/10',
  parallel: 'border-violet-500/30 bg-violet-500/10',
  lint: 'border-emerald-500/25 bg-emerald-500/5',
  telemetry: 'border-white/5 bg-white/[0.02]',
  event: 'border-white/10 bg-white/[0.03]',
};

const KIND_BADGE: Record<MissionEventKind, string> = {
  tool: 'border-white/10 bg-white/5 text-slate-300',
  delegation: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-200',
  subagent: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',
  parallel: 'border-violet-500/30 bg-violet-500/10 text-violet-200',
  lint: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  telemetry: 'border-white/10 bg-white/5 text-slate-400',
  event: 'border-white/10 bg-white/5 text-slate-300',
};

const STATUS_TONE: Record<MissionEventStatus, string> = {
  running: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
  success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  error: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  warning: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  info: 'border-white/10 bg-white/5 text-slate-400',
};

function statusLabel(kind: MissionEventKind, status: MissionEventStatus): string {
  if (kind === 'lint') {
    if (status === 'success') return 'Verified';
    if (status === 'warning') return 'Unverified';
    if (status === 'error') return 'Lint failed';
    return 'Checking';
  }
  if (kind === 'delegation' || kind === 'subagent') {
    if (status === 'success') return 'Handed off';
    if (status === 'error') return 'Failed';
    if (status === 'warning') return 'Check';
    return 'Running';
  }
  if (kind === 'parallel') {
    if (status === 'success') return 'Batch complete';
    if (status === 'error') return 'Batch failed';
    return 'Running';
  }
  if (status === 'success') return 'Done';
  if (status === 'error') return 'Failed';
  if (status === 'warning') return 'Check';
  if (status === 'running') return 'Running';
  return '';
}

export function MissionTimeline({ events, showPayloads = false, limit = 500, emptyText = 'No execution events recorded.' }: MissionTimelineProps) {
  const steps = summarizeMissionEvents(events)
    .filter((step) => showPayloads || !(step.kind === 'event' && step.type === 'action_payload'))
    .slice(-limit);
  if (!steps.length) return <p className="text-slate-500 text-xs italic">{emptyText}</p>;
  return (
    <div className="space-y-1.5 font-mono text-[11px]" data-testid="mission-timeline">
      {steps.map((step, i) => {
        if (step.kind === 'event' && step.type !== 'action_payload') {
          const time = step.time;
          return (
            <div key={i} className={`whitespace-pre-wrap break-words ${eventColor(step.type)}`}>
              {time && <span className="opacity-50 select-none mr-2">[{time}]</span>}
              <span className={step.type === 'reasoning' ? 'italic opacity-80' : undefined}>{step.detail}</span>
            </div>
          );
        }

        if (step.kind === 'event' && step.type === 'action_payload') {
          return (
            <div key={i} className={`whitespace-pre-wrap break-words ${eventColor(step.type)}`}>
              {step.time && <span className="opacity-50 select-none mr-2">[{step.time}]</span>}
              <span className="block pl-4 border-l-2 border-yellow-500/40">{step.detail}</span>
            </div>
          );
        }

        if (step.kind === 'telemetry') {
          return (
            <div key={i} className="flex flex-wrap items-center gap-1.5" data-testid="mission-telemetry">
              {step.time && <span className="mr-1 text-slate-600 select-none">[{step.time}]</span>}
              <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-slate-400">
                {step.label}
              </span>
              {step.detail && <span className="min-w-0 truncate text-[10px] text-slate-500">{step.detail}</span>}
            </div>
          );
        }

        const status = statusLabel(step.kind, step.status);
        return (
          <div
            key={i}
            data-testid="mission-step"
            data-kind={step.kind}
            data-status={step.status}
            className={`rounded-md border px-2 py-1.5 ${KIND_TONE[step.kind]}`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              {step.time && <span className="text-slate-600 select-none">[{step.time}]</span>}
              <span className={`rounded border px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider ${KIND_BADGE[step.kind]}`}>
                {step.label}
              </span>
              {step.detail && <span className="min-w-0 flex-1 truncate text-slate-300">{step.detail}</span>}
              {status && (
                <span
                  data-testid="mission-status"
                  className={`ml-auto shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider ${STATUS_TONE[step.status]}`}
                >
                  {status}
                </span>
              )}
            </div>
            {showPayloads && step.payload && (
              <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words border-l-2 border-yellow-500/40 pl-3 text-[10px] text-yellow-200/70">
                {step.payload}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}

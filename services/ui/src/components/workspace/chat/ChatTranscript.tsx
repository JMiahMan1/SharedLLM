import { useEffect, useState } from 'react';
import { AlertCircle, ArrowUpRight, Bot, Brain, Check, ChevronRight, GitBranch, Loader2, Square, Wrench } from 'lucide-react';
import MarkdownViewer from '../../MarkdownViewer';
import { api } from '../../../services/api';
import type { RavenMission, WorkspaceChatMessage, WorkspaceChatPart } from '../../../types/api';
import { ASK_MODE_CHIPS, RESOLVED_MODE_LABEL } from './askModes';
import { missionIdFromInput, missionIdFromOutput, toolPresentation, toolSummary, type LiveTurn } from './chatParts';
import RavenMissionDetail from '../../settings/RavenMissionDetail';

const TERMINAL_MISSION = new Set(['completed', 'complete', 'failed', 'killed', 'cancelled', 'canceled', 'error']);

/** A chat's messages, plus the turn streaming right now. */
export default function ChatTranscript({
  messages,
  live,
  onHandToRaven,
}: {
  messages: WorkspaceChatMessage[];
  live: LiveTurn | null;
  onHandToRaven?: (message: WorkspaceChatMessage) => void;
}) {
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
  return (
    <div className="space-y-5" data-testid="chat-transcript">
      {messages.map((message, index) =>
        message.role === 'user' ? (
          <UserMessage key={message.id ?? `u-${index}`} message={message} />
        ) : (
          <AssistantMessage
            key={message.id ?? `a-${index}`}
            parts={message.parts}
            meta={message.meta}
            onHandToRaven={
              !live && message === lastAssistant && onHandToRaven && message.meta?.resolved_mode !== 'raven' && message.meta?.status === 'done'
                ? () => onHandToRaven(message)
                : undefined
            }
          />
        ),
      )}
      {live && (
        <AssistantMessage
          parts={live.liveText ? [...live.parts, { type: 'text', text: live.liveText }] : live.parts}
          meta={live.meta}
          streaming
        />
      )}
    </div>
  );
}

function UserMessage({ message }: { message: WorkspaceChatMessage }) {
  const text = message.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n');
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-indigo-600/80 px-3.5 py-2 text-sm text-white">{text}</div>
    </div>
  );
}

function AssistantMessage({
  parts,
  meta,
  streaming = false,
  onHandToRaven,
}: {
  parts: WorkspaceChatPart[];
  meta?: WorkspaceChatMessage['meta'];
  streaming?: boolean;
  onHandToRaven?: () => void;
}) {
  const lastIndex = parts.length - 1;
  return (
    <div className="flex gap-2.5">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-fuchsia-500 to-indigo-500 text-white" aria-hidden>
        <Bot size={14} />
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
          {meta?.resolved_mode && (
            <span className={`rounded border px-1.5 py-0.5 uppercase tracking-wide ${ASK_MODE_CHIPS[meta.resolved_mode]}`}>
              {RESOLVED_MODE_LABEL[meta.resolved_mode]}
            </span>
          )}
          {meta?.model && <span>{meta.model}</span>}
          {!!meta?.context_chars && <span>· {meta.context_chars.toLocaleString()} chars retrieved</span>}
          {meta?.status === 'aborted' && <span className="text-amber-300">· stopped</span>}
          {streaming && parts.length === 0 && (
            <span className="flex items-center gap-1 text-indigo-300">
              <Loader2 size={11} className="animate-spin" /> Working…
            </span>
          )}
        </div>
        {parts.map((part, index) => (
          <Part key={index} part={part} active={streaming && index === lastIndex} />
        ))}
        {onHandToRaven && (
          <button
            type="button"
            onClick={onHandToRaven}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600/20 px-2.5 py-1 text-[11px] text-indigo-200 hover:bg-indigo-600/30"
          >
            <Bot size={12} /> Hand this to Raven
          </button>
        )}
      </div>
    </div>
  );
}

function Part({ part, active }: { part: WorkspaceChatPart; active: boolean }) {
  switch (part.type) {
    case 'reasoning':
      return <ReasoningPart text={part.text} active={active} />;
    case 'tool':
      return <ToolPart part={part} />;
    case 'mission':
      return <MissionPart missionId={part.mission_id} />;
    case 'error':
      return (
        <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200" role="alert">
          <AlertCircle size={14} className="mt-0.5 shrink-0" /> {part.text}
        </div>
      );
    default:
      return (
        <div className="text-sm text-slate-100">
          <MarkdownViewer markdown={part.text} />
        </div>
      );
  }
}

/** Thinking: open while it streams, folded to one readable line afterwards. */
function ReasoningPart({ text, active }: { text: string; active: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const isOpen = open ?? active;
  const preview = text.trim().split('\n').find((line) => line.trim()) ?? '';
  return (
    <div className="rounded-lg border border-white/5 bg-white/[0.03]" data-testid="reasoning-part">
      <button
        type="button"
        onClick={() => setOpen(!isOpen)}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] text-slate-400 hover:text-slate-200"
      >
        <ChevronRight size={12} className={`transition-transform ${isOpen ? 'rotate-90' : ''}`} />
        <Brain size={12} className={active ? 'animate-pulse text-fuchsia-300' : ''} />
        {active ? 'Thinking…' : 'Thought'}
        {!isOpen && text.length > 0 && <span className="ml-auto text-[10px] text-slate-600">{text.length.toLocaleString()} chars</span>}
      </button>
      {isOpen ? (
        <div className="max-h-64 overflow-y-auto px-3 pb-2 custom-scrollbar">
          <p className="whitespace-pre-wrap text-[12px] italic leading-relaxed text-slate-400">{text}</p>
        </div>
      ) : (
        preview && <p className="truncate px-3 pb-2 text-[11px] italic text-slate-500">{preview}</p>
      )}
    </div>
  );
}

function statusIcon(status: Extract<WorkspaceChatPart, { type: 'tool' }>['status']) {
  if (status === 'running') return <Loader2 size={12} className="animate-spin text-sky-300" />;
  if (status === 'done') return <Check size={12} className="text-emerald-300" />;
  if (status === 'aborted') return <Square size={11} className="text-amber-300" />;
  return <AlertCircle size={12} className="text-rose-300" />;
}

function ToolPart({ part }: { part: Extract<WorkspaceChatPart, { type: 'tool' }> }) {
  const [open, setOpen] = useState(false);
  const presentation = toolPresentation(part.name);
  if (presentation.kind !== 'tool') return <DelegationPart part={part} presentation={presentation} />;

  const summary = toolSummary(part.input);
  const missionId = part.mission_id ?? missionIdFromOutput(part.output) ?? missionIdFromInput(part.input);
  return (
    <div className="rounded-lg border border-white/10 bg-black/30" data-testid="tool-part">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px]">
        <ChevronRight size={12} className={`shrink-0 text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`} />
        <Wrench size={12} className="shrink-0 text-slate-400" />
        <span className="shrink-0 font-medium text-slate-200">{presentation.label}</span>
        {summary && <span className="truncate font-mono text-[11px] text-slate-500">{summary}</span>}
        <span className="ml-auto shrink-0">{statusIcon(part.status)}</span>
      </button>
      {missionId != null && (
        <div className="px-2.5 pb-1.5">
          <MissionLink missionId={missionId} />
        </div>
      )}
      {open && (
        <div className="space-y-2 border-t border-white/5 px-3 py-2">
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-slate-400">{JSON.stringify(part.input, null, 2)}</pre>
          {part.output !== undefined && (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 font-mono text-[11px] text-slate-300">{part.output}</pre>
          )}
        </div>
      )}
    </div>
  );
}

/** A cross-harness handoff: who took the work, what it was, and where it lives. */
function DelegationPart({
  part,
  presentation,
}: {
  part: Extract<WorkspaceChatPart, { type: 'tool' }>;
  presentation: ReturnType<typeof toolPresentation>;
}) {
  const [open, setOpen] = useState(false);
  const summary = toolSummary(part.input);
  const missionId = part.mission_id ?? missionIdFromOutput(part.output) ?? missionIdFromInput(part.input);
  return (
    <div className={`rounded-lg border ${presentation.tone}`} data-testid="delegation-part">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px]">
        <ChevronRight size={12} className={`shrink-0 opacity-60 transition-transform ${open ? 'rotate-90' : ''}`} />
        {presentation.kind === 'subagent' ? <Bot size={12} className="shrink-0" /> : <GitBranch size={12} className="shrink-0" />}
        <span className="shrink-0 font-semibold">{presentation.label}</span>
        {presentation.targetLabel && (
          <span className="shrink-0 rounded border border-current/30 bg-black/20 px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wider opacity-80">
            {presentation.targetLabel}
          </span>
        )}
        {summary && <span className="truncate font-mono text-[11px] opacity-70">{summary}</span>}
        <span className="ml-auto shrink-0">{statusIcon(part.status)}</span>
      </button>
      {missionId != null && (
        <div className="px-2.5 pb-1.5">
          <MissionLink missionId={missionId} />
        </div>
      )}
      {open && (
        <div className="space-y-2 border-t border-current/10 px-3 py-2">
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] opacity-80">{JSON.stringify(part.input, null, 2)}</pre>
          {part.output !== undefined && (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 font-mono text-[11px] opacity-90">{part.output}</pre>
          )}
        </div>
      )}
    </div>
  );
}

/** A clean, clickable way into the Raven mission a tool result points at. */
function MissionLink({ missionId }: { missionId: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="mission-link"
        className="inline-flex items-center gap-1 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-indigo-200 hover:bg-indigo-500/20"
      >
        <ArrowUpRight size={11} /> Open mission #{missionId}
      </button>
      <RavenMissionDetail missionId={open ? missionId : null} onClose={() => setOpen(false)} />
    </>
  );
}

/** A Raven mission's progress, polled until it finishes. */
function MissionPart({ missionId }: { missionId: number }) {
  const [mission, setMission] = useState<RavenMission | null>(null);
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const m = await api.getRavenMission(missionId);
        if (!live) return;
        setMission(m);
        if (!TERMINAL_MISSION.has(String(m.status).toLowerCase())) timer = window.setTimeout(poll, 5000);
      } catch {
        if (live) timer = window.setTimeout(poll, 15000);
      }
    };
    void poll();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [missionId]);
  const status = String(mission?.status ?? 'queued').toLowerCase();
  const done = TERMINAL_MISSION.has(status);
  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/10 p-3 text-xs" data-testid="mission-part">
      <div className="flex items-center gap-2 text-indigo-100">
        {done ? <Check size={13} /> : <Loader2 size={13} className="animate-spin" />}
        <span className="font-semibold">Raven mission #{missionId}</span>
        <span className="ml-auto uppercase tracking-wide text-[10px] text-indigo-300">{status}</span>
      </div>
      {typeof mission?.progress === 'number' && !done && (
        <div className="mt-2 h-1 overflow-hidden rounded bg-white/10">
          <div className="h-full bg-indigo-400 transition-all" style={{ width: `${Math.max(3, Math.min(100, mission.progress))}%` }} />
        </div>
      )}
      {mission?.last_llm_reply && (
        <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-[11px] text-slate-300">{mission.last_llm_reply}</p>
      )}
      <div className="mt-2">
        <MissionLink missionId={missionId} />
      </div>
    </div>
  );
}

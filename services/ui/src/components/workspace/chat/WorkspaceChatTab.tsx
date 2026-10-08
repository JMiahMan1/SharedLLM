import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, MessageSquare } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../../services/api';
import type { WorkspaceAskMode, WorkspaceChat, WorkspaceChatMessage } from '../../../types/api';
import ChatComposer from './ChatComposer';
import ChatTranscript from './ChatTranscript';
import { emptyTurn, foldEvent, type LiveTurn } from './chatParts';

interface WorkspaceChatTabProps {
  workspaceId: string;
  chatId: string;
  /** A first message to send as soon as the tab opens (a chat started from the sidebar). */
  initial?: { message: string; mode: WorkspaceAskMode } | null;
  onInitialSent?: () => void;
  onTitle?: (title: string) => void;
  onRunningChange?: (running: boolean) => void;
}

/**
 * One workspace chat, open as a tab: the conversation so far, the turn
 * streaming now (thinking, tool steps, text), and a composer to answer.
 *
 * Modelled on the coding-agent harnesses: messages are typed parts (OpenCode),
 * a message typed while a turn runs is queued and sent after it (Pi's
 * follow-up), and Stop ends the turn on the server too.
 */
export default function WorkspaceChatTab({ workspaceId, chatId, initial, onInitialSent, onTitle, onRunningChange }: WorkspaceChatTabProps) {
  const [chat, setChat] = useState<WorkspaceChat | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [queue, setQueue] = useState<{ message: string; mode: WorkspaceAskMode }[]>([]);
  const queueRef = useRef<{ message: string; mode: WorkspaceAskMode }[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  const initialSentRef = useRef(false);

  const running = live !== null;

  // The parent passes fresh callbacks each render; read the latest without
  // making them effect dependencies (that reloaded the chat on every render).
  const callbacks = useRef({ onTitle, onRunningChange, onInitialSent });
  useEffect(() => {
    callbacks.current = { onTitle, onRunningChange, onInitialSent };
  });

  useEffect(() => {
    callbacks.current.onRunningChange?.(running);
  }, [running]);

  useEffect(() => {
    let live_ = true;
    api
      .getWorkspaceChat(workspaceId, chatId)
      .then((c) => {
        if (!live_) return;
        setChat(c);
        callbacks.current.onTitle?.(c.title);
      })
      .catch((e: unknown) => live_ && setLoadError(e instanceof Error ? e.message : 'Could not load this chat'));
    return () => {
      live_ = false;
    };
  }, [workspaceId, chatId]);

  // Leaving the tab for good stops its turn, like closing a terminal stops its job.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Follow the conversation while the reader is at the bottom; leave them be if they scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [chat?.messages.length, live]);

  const runTurn = useCallback(
    async (message: string, mode: WorkspaceAskMode): Promise<'finished' | 'stopped'> => {
      const controller = new AbortController();
      abortRef.current = controller;
      stickRef.current = true;
      const userMessage: WorkspaceChatMessage = { role: 'user', parts: [{ type: 'text', text: message }] };
      setChat((c) => (c ? { ...c, messages: [...c.messages, userMessage] } : c));
      setLive(emptyTurn());
      let finished: WorkspaceChatMessage | null = null;
      let turn = emptyTurn();
      try {
        await api.streamWorkspaceChatTurn(
          workspaceId,
          chatId,
          { message, mode },
          (event) => {
            if (event.type === 'done' || event.type === 'error') {
              finished = event.message ?? null;
              if (event.type === 'error') toast.error(event.text);
              return;
            }
            turn = foldEvent(turn, event);
            setLive(turn);
          },
          controller.signal,
        );
      } catch (e: unknown) {
        if (!controller.signal.aborted) toast.error(e instanceof Error ? e.message : 'The turn failed');
      }
      // The stored reply is authoritative; if it never arrived (stopped, or the
      // stream broke), keep what streamed so the work is not lost from view.
      const fallback: WorkspaceChatMessage = {
        role: 'assistant',
        parts: turn.liveText ? [...turn.parts, { type: 'text', text: turn.liveText }] : turn.parts,
        meta: { ...turn.meta, status: controller.signal.aborted ? 'aborted' : 'error' },
      };
      setChat((c) => (c ? { ...c, messages: [...c.messages, finished ?? fallback] } : c));
      setLive(null);
      abortRef.current = null;
      // The first message names the chat on the server; pick that up for the tab.
      api.getWorkspaceChat(workspaceId, chatId).then((c) => callbacks.current.onTitle?.(c.title)).catch(() => {});
      return controller.signal.aborted ? 'stopped' : 'finished';
    },
    [workspaceId, chatId],
  );

  /**
   * Run a turn, then each queued follow-up in order (Pi's follow-up queue).
   * Stop pauses the queue: whatever is queued waits for the next turn you send.
   */
  const send = useCallback(
    async (message: string, mode: WorkspaceAskMode) => {
      let next: { message: string; mode: WorkspaceAskMode } | undefined = { message, mode };
      while (next) {
        const outcome = await runTurn(next.message, next.mode);
        next = outcome === 'stopped' ? undefined : queueRef.current.shift();
        setQueue([...queueRef.current]);
      }
    },
    [runTurn],
  );

  useEffect(() => {
    if (!initial || initialSentRef.current || !chat) return;
    initialSentRef.current = true;
    callbacks.current.onInitialSent?.();
    void send(initial.message, initial.mode);
  }, [initial, chat, send]);

  const submit = (message: string, mode: WorkspaceAskMode) => {
    if (running) {
      queueRef.current = [...queueRef.current, { message, mode }];
      setQueue(queueRef.current);
    } else {
      void send(message, mode);
    }
  };

  const handToRaven = (message: WorkspaceChatMessage) => {
    const answer = message.parts.filter((p) => p.type === 'text').map((p) => (p as { text: string }).text).join('\n');
    const question = [...(chat?.messages ?? [])].reverse().find((m) => m.role === 'user');
    const asked = question?.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n') ?? '';
    // Brief Raven with the question and the answer, so the mission does not
    // start the research over from nothing.
    submit(
      [
        'Carry this out as a mission. A quick pass already answered it; reuse what it found.',
        '',
        `Question: ${asked}`,
        '',
        `Answer so far: ${answer.slice(0, 4000)}`,
      ].join('\n'),
      'raven',
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="workspace-chat-tab">
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        }}
        className="custom-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6"
      >
        <div className="mx-auto max-w-3xl">
          {loadError ? (
            <p className="text-sm text-rose-300">{loadError}</p>
          ) : !chat ? (
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 size={14} className="animate-spin" /> Loading chat…
            </p>
          ) : chat.messages.length === 0 && !live ? (
            <div className="py-16 text-center text-slate-500">
              <MessageSquare className="mx-auto mb-3 text-slate-600" size={28} />
              <p className="text-sm">Ask a question or describe a task for this workspace.</p>
              <p className="mt-1 text-xs">Jarvis shows its thinking and each tool it uses as it works.</p>
            </div>
          ) : (
            <ChatTranscript messages={chat.messages} live={live} onHandToRaven={handToRaven} />
          )}
        </div>
      </div>
      <div className="shrink-0 border-t border-white/10 bg-[#0c1120] px-4 py-3 sm:px-6">
        <div className="mx-auto max-w-3xl">
          <ChatComposer
            onSend={submit}
            running={running}
            onStop={() => abortRef.current?.abort()}
            queued={queue.map((q) => q.message)}
            onUnqueue={(i) => {
              queueRef.current = queueRef.current.filter((_, idx) => idx !== i);
              setQueue(queueRef.current);
            }}
            autoFocus
          />
        </div>
      </div>
    </div>
  );
}

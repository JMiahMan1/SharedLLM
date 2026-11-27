import { useEffect, useState, useMemo, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Mic, MicOff, Phone, RefreshCw, Users, Video } from 'lucide-react';
import { api } from '../../services/api';
import { EchoDropInClient } from '../../lib/echoDropInClient';
import { extractEchoFleet, type EchoDeviceSummary } from '../../lib/echoFleet';
import type { ExecutionResponse, TalkConversation } from '../../types/api';
import { useCall } from '../../context/CallContext';
import { detailList } from '../chat/chatModel';

/** How often the room list refreshes so new conversations appear. */
const REFRESH_MS = 15_000;

/**
 * One-tap family calls, entirely inside Jarvis.
 *
 * The conversation list still comes from Nextcloud Talk because that is where
 * the family's rooms live, but the call itself is a peer-to-peer WebRTC call
 * signalled by the Jarvis gateway. A room token is only the shared name both
 * people join; no Nextcloud call is joined or opened in another tab.
 *
 * Calling rings the other members. A device with auto drop-in on (Settings)
 * answers by itself, like an Alexa drop-in; any other gets an incoming call.
 * ``?answer=<room>`` (the Answer button on the phone's call notice) joins
 * that call as soon as the tab opens.
 */
export default function FamilyCalls() {
  const { call, activeCalls, startCall } = useCall();
  const [starting, setStarting] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const answer = searchParams.get('answer');
  // The notice of a video call links with video=1: answering it turns the camera on.
  const answerVideo = searchParams.get('video') === '1';

  useEffect(() => {
    if (!answer) return;
    const params = new URLSearchParams(searchParams);
    params.delete('answer');
    params.delete('video');
    setSearchParams(params, { replace: true });
    const ringing = activeCalls.find((active) => active.token === answer);
    void startCall(answer, ringing?.name || 'Family call', { video: answerVideo || Boolean(ringing?.video), private: ringing?.private });
  }, [answer, answerVideo, activeCalls, searchParams, setSearchParams, startCall]);
  const {
    data: conversations = [],
    isLoading,
    isError,
    refetch,
  } = useQuery<ExecutionResponse, Error, TalkConversation[]>({
    queryKey: ['talk-conversations'],
    // A FAILURE body used to render as an empty list, so a broken Nextcloud
    // looked like a family with no conversations at all.
    queryFn: async () => {
      const response = await api.getTalkConversations();
      if (response.status === 'FAILURE') {
        throw new Error(response.message || 'Could not load conversations');
      }
      return response;
    },
    refetchInterval: REFRESH_MS,
    select: (response) => detailList<TalkConversation>(response, 'conversations').filter((c) => c.token && c.type !== 6),
  });

  const activeByToken = new Map(activeCalls.map((active) => [active.token, active]));
  const sorted = [...conversations].sort((a, b) => {
    const activeA = activeByToken.has(a.token);
    const activeB = activeByToken.has(b.token);
    if (activeA !== activeB) return activeA ? -1 : 1;
    if ((a.type === 1) !== (b.type === 1)) return a.type === 1 ? 1 : -1;
    return (b.last_activity ?? 0) - (a.last_activity ?? 0);
  });

  const start = async (conversation: TalkConversation, video = false) => {
    setStarting(conversation.token);
    try {
      await startCall(conversation.token, conversation.display_name, { video, private: conversation.private });
    } finally {
      setStarting(null);
    }
  };

  // ---- Echo rooms (Home Hub Drop-In) ----

  const {
    data: entities = [],
    isLoading: entitiesLoading,
    error: entitiesError,
  } = useQuery<Record<string, unknown>[], Error>({
    queryKey: ['echo-rooms', 'entities'],
    queryFn: async () => {
      // api.getEntities() returns the entity array directly.
      return (await api.getEntities()) as Record<string, unknown>[];
    },
    retry: false,
  });

  const fleet = useMemo(() => extractEchoFleet(entities as Array<{ entity_id: string; state: string; friendly_name?: string; attributes?: Record<string, unknown> }>), [entities]);
  // Only Echo satellites (a published assist_satellite or drop-in sensor) are
  // real Echo rooms; households without any Echo render nothing extra.
  const echoRooms = fleet.filter((device) => Boolean(device.satelliteEntityId || device.dropInEntityId));

  const clientRef = useRef<EchoDropInClient | null>(null);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [dropInError, setDropInError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [dropInMuted, setDropInMuted] = useState(false);

  const activeRoom = echoRooms.find((device) => device.node === activeNode);

  const dropInActive = (device: EchoDeviceSummary): boolean =>
    device.dropInState === 'connected' || device.dropInState === 'calling' || device.dropInState === 'ringing';

  const roomStatus = (device: EchoDeviceSummary): 'Ready' | 'DND' | 'Drop-In active' | 'Offline' => {
    if (!device.online) return 'Offline';
    if (device.dndOn) return 'DND';
    if (dropInActive(device)) return 'Drop-In active';
    return 'Ready';
  };

  const statusBadge = (status: 'Ready' | 'DND' | 'Drop-In active' | 'Offline'): React.ReactNode => {
    switch (status) {
      case 'Ready':
        return <span className="shrink-0 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-300">Ready</span>;
      case 'DND':
        return <span className="shrink-0 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-amber-300">DND</span>;
      case 'Drop-In active':
        return <span className="shrink-0 rounded-full bg-indigo-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-indigo-300">Drop-In active</span>;
      case 'Offline':
        return <span className="shrink-0 rounded-full bg-red-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-red-300">Offline</span>;
    }
  };

  const tearDownClient = (): void => {
    clientRef.current?.disconnect();
    clientRef.current = null;
    setActiveNode(null);
    setConnecting(false);
    setDropInMuted(false);
  };

  const startDropIn = async (device: EchoDeviceSummary) => {
    if (activeNode && activeNode !== device.node) {
      setDropInError(`A Drop-In to ${activeRoom?.name || activeNode} is already active. End it first.`);
      return;
    }
    if (!device.online) {
      setDropInError(`${device.name} is offline.`);
      return;
    }
    if (device.dndOn) {
      setDropInError(`${device.name} has Do Not Disturb on.`);
      return;
    }
    if (dropInActive(device)) {
      setDropInError(`${device.name} already has an active Drop-In.`);
      return;
    }

    setDropInError(null);
    setConnecting(true);

    const token = localStorage.getItem('jarvis_api_key');
    if (!token) {
      setConnecting(false);
      setDropInError('No API key found. Sign in again to use Drop-In.');
      return;
    }

    const client = new EchoDropInClient({
      node: device.node,
      token,
      onStateChange: (state) => {
        if (state === 'connected') {
          setConnecting(false);
          setActiveNode(device.node);
          setDropInMuted(client.isMuted());
          clientRef.current = client;
        } else if (state === 'error') {
          setConnecting(false);
          clientRef.current = null;
          setActiveNode(null);
          setDropInMuted(false);
        }
      },
      onError: (error) => {
        setConnecting(false);
        clientRef.current = null;
        setActiveNode(null);
        setDropInMuted(false);
        setDropInError(error.message || 'Drop-In failed.');
      },
    });

    try {
      await client.connect();
    } catch (error) {
      setConnecting(false);
      clientRef.current = null;
      setActiveNode(null);
      setDropInMuted(false);
      setDropInError(error instanceof Error ? error.message : 'Drop-In failed.');
    }
  };

  const endDropIn = () => {
    tearDownClient();
    setDropInError(null);
  };

  // ---- render ----

  return (
    <section className="w-full min-w-0 max-w-full space-y-3 overflow-x-hidden" data-testid="family-calls">
      <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-300">
            <Phone size={18} />
          </span>
          <div className="min-w-0">
            <h2 className="font-semibold text-slate-100">Call family</h2>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              Call the family group or one person. Phones with auto drop-in on answer by
              themselves; everyone else gets a ringing call to answer. You can keep using the rest
              of Jarvis while you talk.
            </p>
          </div>
        </div>
      </div>

      {isLoading && conversations.length === 0 && (
        <p className="flex items-center gap-2 px-1 text-sm text-slate-400" data-testid="family-calls-loading">
          <Loader2 size={15} className="animate-spin" /> Loading conversations…
        </p>
      )}

      {isError && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200" data-testid="family-calls-error">
          <p>
            {conversations.length === 0
              ? 'Could not load the family conversations.'
              : 'Could not refresh the family conversations, so this list may be out of date.'}
          </p>
          <button
            type="button"
            onClick={() => refetch()}
            className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-xl bg-rose-500/20 px-3 font-medium text-rose-100"
          >
            <RefreshCw size={14} /> Try again
          </button>
        </div>
      )}

      {!isLoading && !isError && sorted.length === 0 && (
        <p className="px-1 text-sm text-slate-400" data-testid="family-calls-empty">
          No family conversations yet. Create one in Chat to start calling.
        </p>
      )}

      <div className="grid w-full min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
        {sorted.map((conversation) => {
          const group = conversation.type !== 1;
          const Icon = group ? Users : Phone;
          const active = activeByToken.get(conversation.token);
          const inThisCall = call?.room === conversation.token;
          const busy = starting === conversation.token;
          const people = active?.participants.map((person) => person.name).join(', ');
          return (
            <div
              key={conversation.token}
              className="flex min-w-0 max-w-full items-center gap-3 overflow-hidden rounded-2xl border border-white/5 bg-slate-900/60 p-3"
              data-testid={`family-call-${conversation.token}`}
            >
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/[0.07] text-slate-200">
                <Icon size={18} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold text-slate-100">{conversation.display_name}</p>
                <p className={`truncate text-xs ${active ? 'text-emerald-300' : 'text-slate-400'}`}>
                  {inThisCall
                    ? 'You are in this call'
                    : active
                      ? `${people || 'Someone'} in the call`
                      : conversation.can_start_call === false
                        ? 'Not available to call right now'
                        : group
                          ? 'Family group · audio and video'
                          : 'Direct call · audio and video'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void start(conversation)}
                disabled={busy || (!active && conversation.can_start_call === false)}
                aria-label={
                  inThisCall
                    ? `Show the ${conversation.display_name} call`
                    : active
                      ? `Join the ${conversation.display_name} call`
                      : `Call ${conversation.display_name}`
                }
                className={`flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  active
                    ? 'bg-emerald-500 text-emerald-950 hover:bg-emerald-400'
                    : 'bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]'
                }`}
              >
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Phone size={15} />}
                {busy || inThisCall || active
                  ? (busy ? 'Calling…' : inThisCall ? 'Open' : 'Join')
                  // On the narrowest phones the icon alone, so the conversation's name keeps its room.
                  : <span className="max-[379px]:sr-only">Call</span>}
              </button>
              {!active && !inThisCall && (
                <button
                  type="button"
                  onClick={() => void start(conversation, true)}
                  disabled={busy || conversation.can_start_call === false}
                  aria-label={`Video call ${conversation.display_name}`}
                  title="Video call"
                  data-testid={`family-video-call-${conversation.token}`}
                  className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl bg-white/[0.08] text-slate-100 transition hover:bg-white/[0.14] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Video size={16} />
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Echo rooms Drop-In section. Renders nothing when there are no Echo satellites. */}
      {echoRooms.length > 0 && (
        <section className="space-y-3" aria-label="Echo rooms">
          <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-4">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-300">
                <Phone size={18} />
              </span>
              <div className="min-w-0">
                <h2 className="font-semibold text-slate-100">Echo rooms</h2>
                <p className="mt-1 text-xs leading-relaxed text-slate-400">
                  Drop in on Echo Dots and Echo Shows adopted through Home Hub. Microphone access is
                  required; nothing is recorded.
                </p>
              </div>
            </div>
          </div>

          {entitiesLoading && echoRooms.length === 0 && (
            <p className="flex items-center gap-2 px-1 text-sm text-slate-400">Loading Echo rooms…</p>
          )}

          {entitiesError && (
            <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
              <p>Could not load Echo rooms from Home Hub.</p>
            </div>
          )}

          <div className="grid w-full min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
            {echoRooms.map((device) => {
              const inThisCall = activeNode === device.node;
              const busy = connecting && activeNode === device.node;
              const status = roomStatus(device);
              const canDropIn = !inThisCall && status !== 'Offline' && status !== 'DND' && !dropInActive(device);
              const disabled = !canDropIn || busy || connecting;
              return (
                <div
                  key={device.node}
                  className="flex min-w-0 max-w-full items-center gap-3 overflow-hidden rounded-2xl border border-white/5 bg-slate-900/60 p-3"
                  data-testid={`echo-room-${device.node}`}
                >
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-indigo-500/10 text-indigo-300">
                    <Phone size={18} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold text-slate-100">{device.name}</p>
                    <p className="truncate text-[11px] text-slate-400">node: {device.node}</p>
                    <div className="mt-1 flex items-center gap-2">
                      {statusBadge(status)}
                      {device.dropInWith && <span className="text-[10px] text-slate-500">with {device.dropInWith}</span>}
                    </div>
                  </div>

                  {inThisCall ? (
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        type="button"
                        aria-label={`Mute microphone for ${device.name}`}
                        disabled={busy}
                        onClick={() => {
                          const client = clientRef.current;
                          if (!client) return;
                          const next = !dropInMuted;
                          client.setMuted(next);
                          setDropInMuted(next);
                        }}
                        className={`flex min-h-11 min-w-11 items-center justify-center rounded-xl px-2 text-sm transition disabled:opacity-50 ${
                          dropInMuted
                            ? 'bg-amber-500/20 text-amber-300'
                            : 'bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]'
                        }`}
                      >
                        {dropInMuted ? <MicOff size={15} className="text-amber-300" /> : <Mic size={15} />}
                      </button>
                      <button
                        type="button"
                        aria-label={`End Drop-In on ${device.name}`}
                        disabled={busy}
                        onClick={endDropIn}
                        className={`flex min-h-11 shrink-0 items-center gap-2 rounded-xl bg-red-500/20 px-3 text-sm font-semibold text-red-200 hover:bg-red-500/30 disabled:opacity-50`}
                      >
                        <Phone size={14} className="text-red-200" /> End Call
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      aria-label={status === 'DND' ? `Do Not Disturb is on for ${device.name}` : status === 'Offline' ? `${device.name} is offline` : `Drop In on ${device.name}`}
                      disabled={disabled}
                      onClick={() => void startDropIn(device)}
                      className={`flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                        canDropIn
                          ? 'bg-indigo-500 text-white hover:bg-indigo-400'
                          : 'bg-white/[0.08] text-slate-500'
                      }`}
                    >
                      {busy ? <Loader2 size={15} className="animate-spin" /> : <Phone size={15} />}
                      {busy ? 'Calling…' : status === 'DND' ? 'DND' : status === 'Offline' ? 'Offline' : 'Drop In'}
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {dropInError && (
            <div className="rounded-xl border border-red-500/40 bg-red-950/50 p-3 text-sm text-red-100">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
              {dropInError}
            </div>
          )}
        </section>
      )}

      <p className="px-1 text-xs leading-relaxed text-slate-500">
        Calls use your microphone and optional camera, and nothing is recorded. Anyone in the
        conversation can join while the call is live.
      </p>
      <Link to="/settings#family-drop-in"
        className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-200 hover:bg-white/10">
        Who can drop in on me
      </Link>
    </section>
  );
}

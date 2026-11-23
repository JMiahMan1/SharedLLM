import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type FormEvent } from 'react';
import { App as CapacitorApp } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { BellRing, Camera, Heart, Check, CheckCheck, Fingerprint, Images, MessageSquare, ImageIcon, KeyRound, Loader2, Lock, Pencil, SendHorizontal, ShieldAlert, Trash2, X } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { IMAGE_TYPES, PrivateVault, VIDEO_CHUNK_BYTES, VIDEO_TYPES, isSealedMessage, type PrivatePayload, type VideoPayload } from '../../lib/privateChatCrypto';
import { isVideoFile, readClip, videoMimeOf, videoProblem } from '../../lib/privateVideoMedia';
import { chatLeft, chatShown, lockNow, pickerDone, pickerOpening, vaultFor } from '../../lib/privateChatSession';
import { sha256Hex } from '../../lib/galleryImport';
import { secureWindowOff, secureWindowOn, type SecureState } from '../../plugins/secureWindow';
import { deviceUnlockEnabled, deviceUnlockError, deviceUnlockPlugin } from '../../plugins/deviceUnlock';
import { keyFromDevice } from '../../lib/privateChatDeviceUnlock';
import LinkText from './LinkText';
import PrivateLinkCard, { PrivateLinkDraft, type DraftPreview } from './PrivateLinkCard';
import { linkPreviewsOn, readLinkPreview, type LinkPreview } from '../../lib/privateLinkPreview';
import PrivateChatSecurity from './PrivateChatSecurity';
import PrivateCamera from './PrivateCamera';
import PrivateVideoRecorder from './PrivateVideoRecorder';
import { PrivateVideoBubble, PrivateVideoPlayer } from './PrivateVideo';
import PrivateGallery from './PrivateGallery';
import BelovedPage from './beloved/BelovedPage';
import { Lily } from './beloved/BelovedArt';
import { useLoveIndicator } from './beloved/useLoveIndicator';
import { useSeenVisit } from './beloved/seen';
import { canSeeBeloved } from '../../lib/beloved/access';
import { noSave, usePrivatePicture } from './usePrivatePicture';
import EnvelopeBody from './EnvelopeBody';
import {
  dayLabel,
  isMine,
  isSystemMessage,
  MAX_ATTACHMENT_BYTES,
  receiptFor,
  sameDay,
  splitLinks,
  timeLabel,
  type TalkConversation,
  type TalkMessage,
} from './chatModel';
import {
  canNudge,
  errorText,
  MAX_EDIT_CHARS,
  myReactionIds,
  openMarks,
  pendingAnimations,
  prefersReducedMotion,
  reactionChips,
  sameReactionIds,
  threadMessages,
  type OpenedMark,
  type PendingAnimation,
  type PrivateChatInfo,
} from './privateChatModel';
import PrivateMessageSheet, { ReactionRow } from './PrivateMessageSheet';
import ReactionBurst from './ReactionBurst';
import { isImeKey } from '../../lib/keyboard';
import { reminderText, replyFlag } from '../../lib/privateChatReminders';

interface PrivateChatThreadProps {
  conversation: TalkConversation;
  /** The chat's key parameters; undefined while they load or when the server refuses them. */
  info?: PrivateChatInfo;
  /** Why `info` is missing (the server's reason), if it is. */
  infoError?: string;
  /** The chat's messages (sealed envelopes, in Talk's message shape), oldest first: ciphertext until unlocked. */
  messages: TalkMessage[];
  myActorIds: ReadonlySet<string>;
  /** A key made moments ago for a chat just created, so its creator is not asked again at once. */
  adoptKey?: CryptoKey;
  /** The adopted key is held now: the panel forgets it, so coming back asks for the password. */
  onKeyAdopted?: () => void;
  /** Puts Beloved on the home screen (the Android app, a launcher that pins); absent elsewhere. */
  onAddBelovedToHome?: () => void;
  /** Whether this chat (by token) is unlocked: the panel keeps it per chat, so a switch never inherits another's. */
  onUnlockedChange: (token: string, unlocked: boolean) => void;
  /** Refetch the thread after a send or a delete. */
  onChanged: () => void;
}

type Opened = PrivatePayload | 'unreadable';


/**
 * The parents' private chat: locked until the password is entered, and locked
 * again the moment it is left (another conversation, the list, the app or tab
 * going to the background). Unlocking derives the key on this device; the
 * messages and pictures are decrypted in memory and dropped on lock.
 */
export default function PrivateChatThread({
  conversation,
  info,
  infoError,
  messages,
  myActorIds,
  adoptKey,
  onKeyAdopted,
  onUnlockedChange,
  onChanged,
  onAddBelovedToHome,
}: PrivateChatThreadProps) {
  // One vault per chat in this app's memory: it outlives this screen by this
  // device's lock setting (privateChatSession), never the app.
  const [vault] = useState(() => vaultFor(conversation.token));
  const subscribe = useCallback((fn: () => void) => vault.subscribe(fn), [vault]);
  const unlocked = useSyncExternalStore(subscribe, () => vault.unlocked);
  const [opened, setOpened] = useState<Record<string, Opened>>({});
  const [secure, setSecure] = useState<SecureState | 'pending'>('pending');
  const token = conversation.token;
  // Beloved, the couple's page: only for the father and mother of this chat (the server checks again).
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000, enabled: info?.beloved === true });
  const belovedAllowed = canSeeBeloved(me, info);
  // The phone's lock is offered by itself once per opening of the chat, never again after a lock.
  const autoPrompted = useRef(false);

  useEffect(() => {
    if (!adoptKey) return;
    vault.adopt(adoptKey);
    onKeyAdopted?.();
  }, [adoptKey, vault, onKeyAdopted]);

  // Leaving (this screen going away, the tab or app going to the background)
  // locks now or starts the lock timer; coming back cancels a pending lock.
  useEffect(() => {
    chatShown(token);
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') chatLeft(token);
      else chatShown(token);
    };
    document.addEventListener('visibilitychange', onVisibility);
    const handles: Array<{ remove: () => Promise<void> }> = [];
    if (Capacitor.isNativePlatform()) {
      void CapacitorApp.addListener('pause', () => chatLeft(token))
        .then((handle) => handles.push(handle))
        .catch(() => {});
      void CapacitorApp.addListener('resume', () => chatShown(token))
        .then((handle) => handles.push(handle))
        .catch(() => {});
    }
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      for (const handle of handles) void handle.remove();
      chatLeft(token);
    };
  }, [token]);

  // Gone from the screen is locked: the panel must not mark it read behind the lock.
  useEffect(() => () => onUnlockedChange(token, false), [token, onUnlockedChange]);

  // Dropped decryptions on lock: nothing readable outlives the key.
  useEffect(() => {
    onUnlockedChange(token, unlocked);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- wiping what was decrypted when the key goes
    if (!unlocked) setOpened({});
  }, [token, unlocked, onUnlockedChange]);

  // No screenshots or recents thumbnail while the chat is on screen (Android).
  useEffect(() => {
    let live = true;
    void secureWindowOn().then((state) => {
      if (live) setSecure(state);
    });
    return () => {
      live = false;
      void secureWindowOff();
    };
  }, []);

  // Decrypt what is new since the last pass.
  useEffect(() => {
    if (!unlocked) return;
    const todo = messages.filter((m) => typeof m.id === 'number' && !(String(m.id) in opened) && isSealedMessage(m.message));
    if (!todo.length) return;
    let live = true;
    void Promise.all(
      todo.map(async (m) => [String(m.id), (await vault.open(m.message).catch(() => null)) ?? 'unreadable'] as const),
    ).then((pairs) => {
      if (live && vault.unlocked) setOpened((current) => ({ ...current, ...Object.fromEntries(pairs) }));
    });
    return () => {
      live = false;
    };
  }, [messages, unlocked, opened, vault]);

  if (!unlocked) {
    return <LockScreen token={token} info={info} infoError={infoError} vault={vault} partner={info?.partner_name} autoPromptedRef={autoPrompted} />;
  }
  return (
    <UnlockedChat
      partnerName={info?.partner_name || 'your partner'}
      partner={info?.partner}
      token={token}
      conversation={conversation}
      messages={messages}
      opened={opened}
      myActorIds={myActorIds}
      vault={vault}
      secure={secure}
      belovedAllowed={belovedAllowed}
      storage={info?.storage}
      onChanged={onChanged}
      onAddBelovedToHome={onAddBelovedToHome}
    />
  );
}

function LockScreen({
  token,
  info,
  infoError,
  vault,
  partner,
  autoPromptedRef,
}: {
  token: string;
  info?: PrivateChatInfo;
  infoError?: string;
  vault: PrivateVault;
  partner?: string;
  autoPromptedRef: { current: boolean };
}) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState('');
  // 'checking' until the phone says whether its lock is set up for this chat; 'phone' offers it first.
  const [method, setMethod] = useState<'checking' | 'phone' | 'password'>(() => (deviceUnlockPlugin() ? 'checking' : 'password'));

  const phoneUnlock = useCallback(async () => {
    const plugin = deviceUnlockPlugin();
    if (!plugin || !info) return;
    setBusy(true);
    setWrong('');
    try {
      vault.adopt(await keyFromDevice(plugin, token, info.verifier));
    } catch (error) {
      // Cancelled, a changed phone lock, anything: the password always works.
      setWrong(deviceUnlockError(error, 'The phone lock did not open the chat.'));
      setMethod('password');
    } finally {
      setBusy(false);
    }
  }, [info, token, vault]);

  useEffect(() => {
    if (method !== 'checking') return;
    let live = true;
    void deviceUnlockEnabled(token).then((enabled) => {
      if (live) setMethod(enabled ? 'phone' : 'password');
    });
    return () => {
      live = false;
    };
  }, [method, token]);

  useEffect(() => {
    if (method !== 'phone' || !info || autoPromptedRef.current) return;
    const timer = window.setTimeout(() => {
      autoPromptedRef.current = true;
      void phoneUnlock();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [method, info, autoPromptedRef, phoneUnlock]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!info || !password || busy) return;
    setBusy(true);
    setWrong('');
    try {
      // The current key record, never a cached one: after a password change only the new password opens it.
      const fresh = await api.getPrivateChat(token);
      const ok = await vault.unlock(password, fresh);
      if (!ok) setWrong('That is not the password for this chat.');
    } catch (error) {
      setWrong(errorText(error, 'Could not unlock the chat.'));
    } finally {
      setPassword('');
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 py-6" data-testid="private-lock" data-deep-link-wait="">
      <form onSubmit={(event) => void submit(event)} className="w-full max-w-sm rounded-3xl border border-rose-300/15 bg-gradient-to-b from-rose-500/10 to-purple-500/5 p-5 text-center">
        <span className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-rose-500/15 text-rose-200">
          <Lock size={24} aria-hidden />
        </span>
        <h3 className="text-lg font-bold text-white">Private chat</h3>
        <p className="mt-1 text-sm leading-relaxed text-slate-300">
          {partner ? `Just you and ${partner}. ` : ''}End to end encrypted: only the two of you can read it, with the password you agreed on.
        </p>
        {infoError ? (
          <p className="mt-4 rounded-xl border border-amber-400/20 bg-amber-500/10 p-3 text-sm text-amber-100" role="alert">
            {infoError}
          </p>
        ) : !info ? (
          <p className="mt-4 flex items-center justify-center gap-2 text-sm text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </p>
        ) : method === 'checking' ? (
          <p className="mt-4 flex items-center justify-center gap-2 text-sm text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </p>
        ) : method === 'phone' ? (
          <>
            <button
              type="button"
              onClick={() => void phoneUnlock()}
              disabled={busy}
              data-testid="private-phone-unlock"
              className="mt-4 flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-rose-600 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Fingerprint size={15} />}
              {busy ? 'Waiting for the phone…' : 'Use phone lock'}
            </button>
            <button
              type="button"
              onClick={() => setMethod('password')}
              disabled={busy}
              data-testid="private-use-password"
              className="mt-2 min-h-11 w-full rounded-xl text-sm font-semibold text-slate-200 hover:bg-white/10 disabled:opacity-50"
            >
              Use password instead
            </button>
          </>
        ) : (
          <>
            <label className="mt-4 block text-left">
              <span className="mb-1 block text-xs font-semibold text-slate-300">Password</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                autoFocus
                disabled={busy}
                aria-invalid={Boolean(wrong)}
                data-testid="private-password"
                className="glass-input min-h-11 w-full text-base sm:text-sm"
              />
            </label>
            {wrong && (
              <p className="mt-2 text-left text-sm text-rose-200" role="alert" data-testid="private-wrong">
                {wrong}
              </p>
            )}
            <button
              type="submit"
              disabled={busy || !password}
              data-testid="private-unlock"
              className="mt-4 flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-rose-600 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}
              {busy ? 'Unlocking…' : 'Unlock'}
            </button>
            <p className="mt-3 text-xs text-slate-400">It locks again whenever you leave it.</p>
          </>
        )}
      </form>
    </div>
  );
}

interface UnlockedChatProps {
  partnerName: string;
  /** The other parent's username (for a backup's keepsake), when known. */
  partner?: string;
  token: string;
  conversation: TalkConversation;
  messages: TalkMessage[];
  opened: Record<string, Opened>;
  myActorIds: ReadonlySet<string>;
  vault: PrivateVault;
  secure: SecureState | 'pending';
  /** Whether the signed-in parent may open Beloved here (one father and one mother). */
  belovedAllowed: boolean;
  /** Where the chat's messages live; a chat still on Talk has Talk's message size, too small for a preview. */
  storage?: 'talk' | 'store';
  onChanged: () => void;
  onAddBelovedToHome?: () => void;
}

/** The first web address in a message being written, or null. */
function firstLink(text: string): string | null {
  const part = splitLinks(text).find((p) => p.kind === 'link');
  return part && part.kind === 'link' ? part.href : null;
}

/** Wait this long after the last change to a link before asking for its preview. */
const PREVIEW_DEBOUNCE_MS = 600;

function tooLarge(error: unknown): boolean {
  return (error as { response?: { status?: number } } | null)?.response?.status === 413;
}

function UnlockedChat({ partnerName, partner, token, conversation, messages, opened, myActorIds, vault, secure, belovedAllowed, storage, onChanged, onAddBelovedToHome }: UnlockedChatProps) {
  const queryClient = useQueryClient();
  const [securityOpen, setSecurityOpen] = useState(false);
  // The in-app camera: a photo, or a video clip (the two share one button and switch modes).
  const [camera, setCamera] = useState<'photo' | 'video' | null>(null);
  // A picked or recorded clip on its way: its encrypted pieces sent so far.
  const [videoSending, setVideoSending] = useState<{ done: number; count: number } | null>(null);
  // The gallery, opened on one picture (a comment's) or on the grid.
  const [gallery, setGallery] = useState<{ blob?: string } | null>(null);
  // A couple's gallery is a reading signal (metadata only), never a page of content.
  useSeenVisit(token, 'gallery', gallery !== null && belovedAllowed);
  // A heart or love note notice opens the chat with ?beloved=1: straight to Beloved once unlocked.
  const [beloved, setBeloved] = useState(() => typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('beloved') === '1');
  // The heart on the bar: hearts and love notes from your spouse you have not seen in Beloved yet.
  const love = useLoveIndicator(token, belovedAllowed);
  const { markSeen } = love;
  useEffect(() => {
    // Whatever arrives while Beloved is open is seen there.
    if (beloved && belovedAllowed) markSeen();
  }, [beloved, belovedAllowed, markSeen]);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  // Link previews made while writing (null: none could be made), the one being made, and the ones dropped with the X.
  const [previews, setPreviews] = useState<ReadonlyMap<string, LinkPreview | null>>(() => new Map());
  const [previewLoading, setPreviewLoading] = useState<string | null>(null);
  const [previewDropped, setPreviewDropped] = useState<ReadonlySet<string>>(() => new Set());
  const [nudging, setNudging] = useState(false);
  const [nudgeSent, setNudgeSent] = useState(false);
  const { trigger } = useHaptics();
  const [selected, setSelected] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ url: string; name: string; message: TalkMessage; own: boolean } | null>(null);
  const [watching, setWatching] = useState<{ video: VideoPayload; message: TalkMessage; own: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // A message of mine being edited: the composer holds its text until saved or cancelled.
  const [editing, setEditing] = useState<{ id: number; original: string } | null>(null);
  const pageVisible = usePageVisible();

  // Edits and reactions: sealed overlays the gateway keeps per message, opened here with the chat's key.
  const marksQuery = useQuery({
    queryKey: ['private-chat-marks', token],
    queryFn: () => api.getPrivateChatMarks(token),
    retry: false,
    refetchInterval: pageVisible ? 5_000 : false,
  });
  const refetchMarks = marksQuery.refetch;
  const [marks, setMarks] = useState<Record<number, OpenedMark>>({});
  useEffect(() => {
    const list = marksQuery.data?.marks;
    if (!list) return;
    let live = true;
    void openMarks(list, (sealed) => vault.openRecord(sealed)).then((opened) => {
      if (live && vault.unlocked) setMarks(opened);
    });
    return () => {
      live = false;
    };
  }, [marksQuery.data, vault]);

  // Only what was said in the chat: a picture added straight to the gallery is never part of the thread.
  const visible = threadMessages(messages, opened);
  const mine = visible.filter((m) => !isSystemMessage(m) && isMine(m, myActorIds));
  const myLatest = mine[mine.length - 1];
  const nudgeable = canNudge(visible.filter((m) => !isSystemMessage(m)), myActorIds, conversation.last_common_read);

  // Reply reminders: whether the other parent's last messages ask something is decided here, from the text
  // this device holds, and only that one bit goes to the server, once per message (lib/privateChatReminders.ts).
  // Sent (the server has it) and on their way: a message is only done once the server took it,
  // so a failed hint is sent again on the next change to the thread instead of being lost.
  const flagged = useRef(new Set<number>());
  const flagging = useRef(new Set<number>());
  useEffect(() => {
    if (storage !== 'store' || !pageVisible) return;
    const items = visible
      .filter((m) => !isSystemMessage(m) && typeof m.id === 'number')
      .map((m) => ({
        id: Number(m.id),
        mine: isMine(m, myActorIds),
        text: reminderText(opened[String(m.id)], marks[Number(m.id)]?.edit?.text),
      }));
    const flag = replyFlag(items);
    if (!flag?.expectsReply) return;
    const id = flag.messageId;
    if (flagged.current.has(id) || flagging.current.has(id)) return;
    flagging.current.add(id);
    api
      .flagPrivateChatExpectsReply(token, id, true)
      .then(
        () => {
          flagged.current.add(id);
        },
        (error: unknown) => {
          toast.error(`Could not set the reply reminder (${errorText(error, 'no answer')}). It is tried again on the next change.`, {
            id: `reply-hint-${id}`,
          });
        },
      )
      .finally(() => flagging.current.delete(id));
  }, [visible, opened, marks, myActorIds, storage, pageVisible, token]);

  const lastId = visible[visible.length - 1]?.id;
  const openedCount = Object.keys(opened).length;
  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [lastId, openedCount]);
  // A new message may already carry reactions: ask again whenever the thread moves.
  useEffect(() => {
    void refetchMarks();
  }, [lastId, refetchMarks]);

  // --- Reaction animations: the other parent's, the first time this device shows them; mine as I tap. ---
  const played = useRef(new Set<string>());
  const [queue, setQueue] = useState<Array<PendingAnimation & { local?: boolean }>>([]);
  const [playing, setPlaying] = useState<(PendingAnimation & { local?: boolean }) | null>(null);
  // Asked again when the thread changes, not only when the marks do: the marks can answer before the
  // messages (they are fetched once the chat is unlocked), and a reaction whose bubble was not on screen
  // yet must play when it arrives. An unchanged marks refetch keeps the same data, so it never re-asks.
  const shownCount = visible.length;
  useEffect(() => {
    if (!pageVisible) return;
    const fresh = pendingAnimations(marks, played.current);
    if (!fresh.length) return;
    for (const item of fresh) played.current.add(item.id);
    setQueue((current) => [...current, ...fresh]);
  }, [marks, pageVisible, lastId, shownCount]);

  const firstShownId = Number(visible.find((m) => typeof m.id === 'number')?.id ?? 0);
  const markReactionSeen = useCallback(
    (item: PendingAnimation) => {
      void api
        .markPrivateReactionsSeen(token, sameReactionIds(marks, item))
        .then(() => refetchMarks())
        .catch(() => {
          // It plays once more next time; nothing is lost.
        });
    },
    [marks, refetchMarks, token],
  );
  useEffect(() => {
    if (playing || !queue.length || !pageVisible) return;
    const [next, ...rest] = queue;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- taking the next animation off the queue
    setQueue(rest);
    const bubble = feedRef.current?.querySelector<HTMLElement>(`[data-message-id="${next.messageId}"]`);
    if (!bubble) {
      if (!next.local && firstShownId && next.messageId < firstShownId) {
        // Older than anything on screen: it cannot be shown, so it stops waiting.
        markReactionSeen(next);
      } else if (!next.local) {
        // Not in the thread yet (a message still arriving): it is asked for again on the next refresh.
        played.current.delete(next.id);
      }
      return;
    }
    bubble.scrollIntoView?.({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    setPlaying(next);
  }, [playing, queue, pageVisible, firstShownId, markReactionSeen]);

  const burstDone = useCallback(() => {
    const item = playing;
    if (item && !item.local) markReactionSeen(item);
    // A short breath between several waiting reactions.
    window.setTimeout(() => setPlaying(null), 160);
  }, [playing, markReactionSeen]);

  const localCount = useRef(0);
  const react = async (messageId: number, emoji: string) => {
    setSelected(null);
    const mine = myReactionIds(marks[messageId]?.reactions, emoji);
    try {
      if (mine.length) {
        for (const id of mine) await api.removePrivateReaction(token, messageId, id);
      } else {
        trigger('light');
        localCount.current += 1;
        const local = { id: `local-${localCount.current}`, messageId, emoji, local: true };
        // Mine plays now, ahead of anything waiting.
        setQueue((current) => [local, ...current]);
        await api.addPrivateReaction(token, messageId, await vault.sealRecord({ t: 'reaction', m: messageId, e: emoji }));
      }
    } catch (error) {
      toast.error(errorText(error, 'Could not react'));
    } finally {
      void refetchMarks();
    }
  };

  // The link to preview: the first one being written, unless this device has previews off, the
  // message is an edit, the chat is still on Talk, or the X dropped it. Nothing is asked otherwise.
  const link = useMemo(() => firstLink(text), [text]);
  const wantedLink = link && !editing && storage !== 'talk' && linkPreviewsOn(token) && !previewDropped.has(link) ? link : null;
  useEffect(() => {
    if (!wantedLink || previews.has(wantedLink)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setPreviewLoading(wantedLink);
      const settle = (made: LinkPreview | null, problem?: string) => {
        if (controller.signal.aborted) return;
        // No preview: the message goes without one, and the writer is told why rather than left guessing.
        if (problem) toast.error(`No preview for this link: ${problem}`, { id: `link-preview-${wantedLink}` });
        setPreviews((known) => new Map(known).set(wantedLink, made));
        setPreviewLoading((now) => (now === wantedLink ? null : now));
      };
      api.getPrivateLinkPreview(token, wantedLink, controller.signal).then(
        (raw) => {
          const made = readLinkPreview(raw);
          if (made && made.url === wantedLink) settle(made);
          else settle(null, 'the server sent a preview this app cannot read.');
        },
        (error: unknown) => settle(null, errorText(error, 'it could not be made.')),
      );
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [wantedLink, previews, token]);
  const madePreview = wantedLink ? previews.get(wantedLink) : undefined;
  const draft: DraftPreview | null = !wantedLink
    ? null
    : madePreview
      ? { status: 'ready', url: wantedLink, preview: madePreview }
      : madePreview === undefined && previewLoading === wantedLink
        ? { status: 'loading', url: wantedLink }
        : null;

  const startEdit = (messageId: number, current: string) => {
    setSelected(null);
    setEditing({ id: messageId, original: current });
    setText(current);
    window.setTimeout(() => {
      const input = inputRef.current;
      if (!input) return;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }, 0);
  };

  const cancelEdit = () => {
    setEditing(null);
    setText('');
  };

  const saveEdit = async (target: { id: number; original: string }) => {
    const body = text.trim();
    if (!body) {
      toast.error('A message cannot be empty. Delete it instead.');
      return;
    }
    if (body.length > MAX_EDIT_CHARS) {
      toast.error('That is too long for one message.');
      return;
    }
    if (body === target.original.trim()) {
      cancelEdit();
      return;
    }
    setSending(true);
    try {
      await api.editPrivateMessage(token, target.id, await vault.sealRecord({ t: 'edit', m: target.id, text: body }));
      cancelEdit();
      await refetchMarks();
    } catch (error) {
      toast.error(errorText(error, 'Could not save the edit'));
    } finally {
      setSending(false);
    }
  };

  /** Seal and send a message; `blobs` are the encrypted blobs it is made of, deleted with it. */
  const post = async (payload: PrivatePayload, blobs?: string[]) => {
    const message = await vault.seal(payload);
    const res = await api.sendPrivateMessage(token, message, undefined, blobs);
    if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Message failed to send');
  };

  const sendText = async (event: FormEvent) => {
    event.preventDefault();
    if (editing) {
      if (!sending) await saveEdit(editing);
      return;
    }
    const body = text.trim();
    if (!body || sending) return;
    // Only a preview that is ready goes: a slow one is not waited for, and the message goes without it.
    const preview = draft?.status === 'ready' && body.includes(draft.url) ? draft.preview : undefined;
    setSending(true);
    try {
      try {
        await post({ t: 'text', text: body, ...(preview ? { preview } : {}) });
      } catch (error) {
        // A server from before previews refuses a message this size: send the words alone.
        if (!preview || !tooLarge(error)) throw error;
        await post({ t: 'text', text: body });
        toast.error('Sent without the link preview: the server refused a message that size.');
      }
      setText('');
      setPreviewDropped(new Set());
      onChanged();
    } catch (error) {
      toast.error(errorText(error, 'Message failed to send'));
    } finally {
      setSending(false);
    }
  };

  /**
   * Send picture bytes: encrypted here before anything leaves the device,
   * uploaded as an opaque blob (never a Talk file share), and the plaintext
   * copy overwritten with zeros once encrypted.
   */
  const sendImageBytes = async (plain: Uint8Array<ArrayBuffer>, mime: string, name?: string, takenAt?: number) => {
    let blob = '';
    try {
      let sealed: Uint8Array<ArrayBuffer>;
      let sha256 = '';
      try {
        // A fingerprint of the original, inside the encryption, so an import into the gallery skips it.
        sha256 = await sha256Hex(plain);
        sealed = await vault.encrypt(plain);
      } finally {
        plain.fill(0);
      }
      blob = (await api.uploadPrivateBlob(token, sealed)).id;
      // When it was sent (and taken) travels inside the encryption, for the gallery.
      await post({ t: 'image', blob, mime, ...(name ? { name } : {}), sent_at: Math.floor(Date.now() / 1000), ...(takenAt ? { taken_at: takenAt } : {}), sha256 }, [blob]);
      onChanged();
    } catch (error) {
      if (blob) void api.deletePrivateBlob(token, blob).catch(() => {});
      throw error;
    }
  };

  /**
   * Send a video clip. Everything about it is read here (length, frame size,
   * a poster frame drawn to a canvas), then the poster is encrypted like a
   * picture and the clip in 4 MiB pieces (lib/privateChatCrypto.ts), each its
   * own opaque blob. The message names them all inside its encryption. If
   * anything fails, what was uploaded is deleted again.
   */
  const sendVideo = async (source: Blob, mime: string, options: { name?: string; takenAt?: number; duration?: number } = {}, onProgress?: (done: number, count: number) => void) => {
    const uploaded: string[] = [];
    const upload = async (bytes: Uint8Array<ArrayBuffer>) => {
      const id = (await api.uploadPrivateBlob(token, bytes)).id;
      uploaded.push(id);
      return id;
    };
    try {
      const facts = await readClip(source, options.duration);
      let sealedPoster: Uint8Array<ArrayBuffer>;
      try {
        sealedPoster = await vault.encrypt(facts.poster);
      } finally {
        facts.poster.fill(0);
      }
      const poster = await upload(sealedPoster);
      const report = (done: number, count: number) => {
        setVideoSending({ done, count });
        onProgress?.(done, count);
      };
      report(0, Math.max(1, Math.ceil(source.size / VIDEO_CHUNK_BYTES)));
      const sealed = await vault.encryptVideo(source, upload, report);
      const payload: VideoPayload = {
        t: 'video',
        mime,
        size: sealed.size,
        duration: Math.round(facts.duration * 10) / 10,
        ...(facts.width && facts.height ? { width: facts.width, height: facts.height } : {}),
        poster,
        chunks: sealed.chunks,
        clip: sealed.clip,
        chunk_bytes: sealed.chunk_bytes,
        ...(options.name ? { name: options.name } : {}),
        sent_at: Math.floor(Date.now() / 1000),
        ...(options.takenAt ? { taken_at: options.takenAt } : {}),
      };
      await post(payload, [...sealed.chunks, poster]);
      onChanged();
    } catch (error) {
      for (const id of uploaded) void api.deletePrivateBlob(token, id).catch(() => {});
      throw error;
    } finally {
      setVideoSending(null);
    }
  };

  /** A clip from the picker, a paste or a drop. */
  const sendPickedVideo = async (file: File) => {
    const problem = videoProblem(file);
    if (problem) {
      toast.error(problem);
      return;
    }
    setSending(true);
    try {
      // When it was taken is the file's own time: the best a picked clip says without reading its container.
      const takenAt = file.lastModified ? Math.floor(file.lastModified / 1000) : undefined;
      await sendVideo(file, videoMimeOf(file), { name: file.name || undefined, takenAt });
    } catch (error) {
      toast.error(errorText(error, 'Could not send the video'));
    } finally {
      setSending(false);
    }
  };

  /** A photo or a video from the picker, a paste or a drop. Anything else is refused. */
  const sendPicture = async (file: File) => {
    if (isVideoFile(file)) {
      await sendPickedVideo(file);
      return;
    }
    if (!IMAGE_TYPES.has(file.type)) {
      toast.error('Only photos and videos can be sent in a private chat');
      return;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      toast.error('Pictures are limited to 25 MB');
      return;
    }
    setSending(true);
    try {
      await sendImageBytes(new Uint8Array(await file.arrayBuffer()), file.type, file.name);
    } catch (error) {
      toast.error(errorText(error, 'Could not send the picture'));
    } finally {
      setSending(false);
    }
  };

  const sendFiles = async (files: File[]) => {
    for (const file of files) await sendPicture(file);
  };

  /**
   * Delete a message for both of you, and the encrypted blobs it is made of (a
   * picture's, or a clip's pieces and poster). The gateway deletes them with
   * the message too; asking again here also covers a chat still on Talk.
   */
  const removeById = async (messageId: number, blobs: string[] = []) => {
    setSelected(null);
    try {
      const res = await api.deletePrivateMessage(token, messageId);
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Could not delete the message');
      for (const blob of blobs) await api.deletePrivateBlob(token, blob).catch(() => {});
      if (editing?.id === messageId) cancelEdit();
      onChanged();
      // Its edit and reactions went with it on the server.
      void refetchMarks();
    } catch (error) {
      toast.error(errorText(error, 'Could not delete the message'));
    }
  };

  const remove = async (message: TalkMessage) => {
    if (typeof message.id !== 'number') return;
    await removeById(message.id, blobsOf(opened[String(message.id)]));
  };

  const nudge = async () => {
    setNudging(true);
    try {
      await api.nudgePrivateChat(token);
      // The sender feels it go too: a short heartbeat and the heart beating on the button.
      trigger('success');
      setNudgeSent(true);
      window.setTimeout(() => setNudgeSent(false), 1600);
      toast.success('Nudged');
    } catch (error) {
      toast.error(errorText(error, 'Could not nudge'));
    } finally {
      setNudging(false);
    }
  };

  // Comments name a picture by its blob; a video's comments name its poster.
  const videoPosters = new Set(Object.values(opened).flatMap((p) => (p !== 'unreadable' && p.t === 'video' ? [p.poster] : [])));

  // The message whose sheet is open: a readable text message, mine or the other parent's.
  const sheetMessage = visible.find((m, index) => String(m.id ?? index) === selected);
  const sheetPayload = sheetMessage ? opened[String(sheetMessage.id)] : undefined;
  const sheetId = typeof sheetMessage?.id === 'number' ? sheetMessage.id : null;
  const sheetText = sheetPayload && sheetPayload !== 'unreadable' && sheetPayload.t === 'text' ? sheetPayload.text : null;
  const sheetEdit = sheetId !== null ? marks[sheetId]?.edit : undefined;
  const sheetOwn = sheetMessage ? isMine(sheetMessage, myActorIds) : false;

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="private-chat"
      // A dropped photo is encrypted like any other; nothing dropped here is ever opened by the browser.
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes('Files')) event.preventDefault();
      }}
      onDrop={(event) => {
        if (!event.dataTransfer.files.length) return;
        event.preventDefault();
        event.stopPropagation();
        void sendFiles(Array.from(event.dataTransfer.files));
      }}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-white/5 bg-rose-500/[0.06] px-3 py-1.5">
        <Lock size={13} className="shrink-0 text-rose-200" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-xs text-rose-100">Only the two of you can read this</span>
        {nudgeable && (
          <button
            type="button"
            onClick={() => void nudge()}
            disabled={nudging}
            data-testid="private-nudge"
            className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-amber-200 hover:bg-amber-400/10 disabled:opacity-50"
          >
            {nudgeSent ? (
              <Heart size={14} className="fill-rose-400 text-rose-300 motion-safe:animate-[nudge-beat_1.1s_ease-in-out_infinite]" aria-hidden />
            ) : (
              <BellRing size={14} aria-hidden />
            )}{' '}
            {nudgeSent ? 'Nudged' : 'Nudge'}
          </button>
        )}
        {belovedAllowed && (
          <button
            type="button"
            onClick={() => setBeloved(true)}
            aria-label={love.unseen ? `Beloved, ${love.unseen} new from ${partnerName}` : 'Beloved'}
            title={love.unseen ? `${love.unseen} new from ${partnerName}` : 'Beloved'}
            data-testid="private-beloved-open"
            className="relative flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-rose-100 hover:bg-rose-400/10"
          >
            <Lily size={15} /> <span className="hidden sm:inline">Beloved</span>
            {love.unseen > 0 && (
              <span
                className="pointer-events-none absolute right-0.5 top-0.5 flex items-center"
                data-testid="private-love-indicator"
                aria-hidden
              >
                <Heart size={16} className="fill-rose-500 text-rose-400 drop-shadow motion-safe:animate-[nudge-beat_1.6s_ease-in-out_infinite]" />
                <span className="absolute inset-0 flex items-center justify-center pb-px text-[9px] font-bold leading-none text-white">
                  {love.unseen > 9 ? '9+' : love.unseen}
                </span>
              </span>
            )}
          </button>
        )}
        <button
          type="button"
          onClick={() => setGallery({})}
          aria-label="Gallery"
          title="Gallery"
          data-testid="private-gallery-open"
          className="flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-slate-200 hover:bg-white/10"
        >
          <Images size={14} aria-hidden /> <span className="hidden sm:inline">Gallery</span>
        </button>
        <button
          type="button"
          onClick={() => setSecurityOpen(true)}
          aria-label="Lock and password"
          title="Lock and password"
          data-testid="private-security-open"
          className="flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-slate-200 hover:bg-white/10"
        >
          <KeyRound size={14} aria-hidden /> <span className="hidden sm:inline">Security</span>
        </button>
        <button
          type="button"
          onClick={() => lockNow(token)}
          data-testid="private-lock-now"
          className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-slate-200 hover:bg-white/10"
        >
          <Lock size={14} aria-hidden /> Lock
        </button>
      </div>
      {secure === 'unsupported' && (
        <p className="flex shrink-0 items-center gap-2 border-b border-amber-400/20 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-100" role="status" data-testid="private-insecure">
          <ShieldAlert size={14} className="shrink-0" aria-hidden />
          Screenshots are not blocked on this version of the app. Update Jarvis to block them.
        </p>
      )}

      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-3 py-3 sm:px-4" data-testid="private-feed" role="log" aria-label="Messages">
        {visible.length === 0 && <p className="py-10 text-center text-sm text-slate-500">No messages yet. Only the two of you will ever see what you write here.</p>}
        {visible.map((message, index) => {
          const previous = visible[index - 1];
          const newDay = message.timestamp && (!previous || !sameDay(previous.timestamp, message.timestamp));
          const key = String(message.id ?? index);
          const separator = newDay ? (
            <div className="my-3 flex justify-center" role="separator">
              <span className="rounded-full bg-white/5 px-3 py-1 text-[11px] font-medium text-slate-400">{dayLabel(message.timestamp as number)}</span>
            </div>
          ) : null;
          if (isSystemMessage(message)) {
            return (
              <div key={key}>
                {separator}
                <p className="my-2 text-center text-[11px] text-slate-500">
                  <EnvelopeBody raw={message.message} fallback={message.system_message} parameters={message.parameters} />
                </p>
              </div>
            );
          }
          const own = isMine(message, myActorIds);
          const payload = opened[String(message.id)];
          const sealed = isSealedMessage(message.message);
          const isSelected = selected === key;
          const textual = payload !== undefined && payload !== 'unreadable' && payload.t === 'text';
          const messageId = typeof message.id === 'number' ? message.id : null;
          const mark = messageId !== null ? marks[messageId] : undefined;
          // Only a text message can be edited; an edit on anything else is not shown.
          const edit = textual ? mark?.edit : undefined;
          const chips = reactionChips(mark?.reactions ?? []);
          // A text bubble (either parent's) opens the message sheet; a picture opens the viewer and a comment the gallery.
          const selectable = textual && messageId !== null;
          const bursting = playing !== null && messageId !== null && playing.messageId === messageId;
          return (
            <div key={key}>
              {separator}
              <div className={`mb-2 flex flex-col ${own ? 'items-end' : 'items-start'}`}>
                <div
                  data-message-id={messageId ?? undefined}
                  data-focus-id={messageId != null ? `msg-${messageId}` : undefined}
                  role={selectable ? 'button' : undefined}
                  tabIndex={selectable ? 0 : undefined}
                  aria-haspopup={selectable ? 'dialog' : undefined}
                  onClick={() => selectable && setSelected(isSelected ? null : key)}
                  onKeyDown={(event) => {
                    if (selectable && (event.key === 'Enter' || event.key === ' ')) {
                      event.preventDefault();
                      setSelected(isSelected ? null : key);
                    }
                  }}
                  className={`relative max-w-[82%] select-text whitespace-pre-wrap break-words rounded-3xl px-3.5 py-2 text-left text-[15px] leading-snug sm:max-w-[70%] ${
                    own
                      ? 'rounded-br-md bg-gradient-to-br from-rose-600 to-purple-600 text-white'
                      : 'rounded-bl-md border border-white/[0.06] bg-slate-800/90 text-slate-100'
                  } ${isSelected ? 'ring-2 ring-rose-300/70' : ''} ${editing?.id === messageId ? 'ring-2 ring-amber-300/70' : ''} ${
                    bursting ? 'motion-safe:animate-[reaction-bubble_600ms_ease-out]' : ''
                  }`}
                >
                  {!sealed ? (
                    <span className="italic text-amber-100">Not encrypted (sent outside Jarvis): {message.message}</span>
                  ) : payload === undefined ? (
                    <span className="flex items-center gap-2 text-slate-300">
                      <Loader2 size={13} className="animate-spin" /> Decrypting…
                    </span>
                  ) : payload === 'unreadable' ? (
                    <span className="italic opacity-80">This message could not be decrypted.</span>
                  ) : payload.t === 'text' ? (
                    <>
                      <LinkText text={edit ? edit.text : payload.text} />
                      {/* The sender's preview, from inside the message; gone if an edit took its link out. */}
                      {payload.preview && (edit ? edit.text : payload.text).includes(payload.preview.url) && (
                        <PrivateLinkCard preview={payload.preview} own={own} />
                      )}
                    </>
                  ) : payload.t === 'comment' ? (
                    <button
                      type="button"
                      onClick={() => setGallery({ blob: payload.blob })}
                      className="block text-left"
                      data-testid="private-comment-bubble"
                    >
                      <span className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold opacity-80">
                        <MessageSquare size={11} aria-hidden /> {videoPosters.has(payload.blob) ? 'On a video' : 'On a photo'}
                      </span>
                      {payload.text}
                    </button>
                  ) : payload.t === 'image' ? (
                    <PrivatePicture
                      token={token}
                      payload={payload}
                      vault={vault}
                      onOpen={(url) => setViewing({ url, name: payload.name || 'Picture', message, own })}
                    />
                  ) : payload.t === 'video' ? (
                    <PrivateVideoBubble token={token} video={payload} vault={vault} onOpen={() => setWatching({ video: payload, message, own })} />
                  ) : null}
                  {bursting && playing && <ReactionBurst key={playing.id} emoji={playing.emoji} onDone={burstDone} />}
                </div>
                {chips.length > 0 && (
                  <div className={`relative z-10 -mt-1.5 flex max-w-[82%] flex-wrap gap-1 px-2 ${own ? 'justify-end' : 'justify-start'}`}>
                    {chips.map((chip) => (
                      <span
                        key={chip.emoji}
                        role="img"
                        aria-label={`${chip.emoji} from ${chip.names.join(' and ')}`}
                        title={chip.names.join(', ')}
                        data-testid="private-reaction-chip"
                        className={`inline-flex items-center gap-1 rounded-full border-2 border-slate-950 px-1.5 py-px text-[13px] leading-5 shadow-sm ${
                          chip.mine ? 'bg-rose-900/90 text-rose-50' : 'bg-slate-700 text-slate-100'
                        }`}
                      >
                        <span aria-hidden>{chip.emoji}</span>
                        {chip.count > 1 && <span className="text-[11px] font-semibold tabular-nums" aria-hidden>{chip.count}</span>}
                      </span>
                    ))}
                  </div>
                )}
                <span className="mt-0.5 flex items-center gap-1.5 px-2 text-[10px] text-slate-500">
                  {message.timestamp ? timeLabel(message.timestamp) : ''}
                  {edit && (
                    <span title={`Edited ${timeLabel(edit.at)}`} data-testid="private-edited" className="italic">
                      Edited
                    </span>
                  )}
                  {own && message === myLatest && <Receipt read={receiptFor(message, conversation.last_common_read) === 'read'} />}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {editing && (
        <div className="flex shrink-0 items-center gap-2 border-t border-amber-300/20 bg-amber-400/[0.07] pl-3 pr-1" data-testid="private-editing">
          <Pencil size={13} className="shrink-0 text-amber-200" aria-hidden />
          <span className="min-w-0 flex-1 truncate text-xs font-semibold text-amber-100">Editing your message</span>
          <button
            type="button"
            onClick={cancelEdit}
            data-testid="private-edit-cancel"
            className="flex min-h-11 shrink-0 items-center gap-1 rounded-xl px-3 text-xs font-semibold text-slate-200 hover:bg-white/10"
          >
            <X size={13} aria-hidden /> Cancel
          </button>
        </div>
      )}
      {videoSending && !camera && (
        <p className="flex shrink-0 items-center gap-2 border-t border-white/5 bg-slate-900/95 px-3 py-1.5 text-xs text-slate-200" role="status" data-testid="private-video-sending">
          <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden />
          Encrypting and sending the video{videoSending.count > 1 ? `: ${videoSending.done} of ${videoSending.count}` : ''}
        </p>
      )}
      {draft && <PrivateLinkDraft draft={draft} onDismiss={() => setPreviewDropped((dropped) => new Set(dropped).add(draft.url))} />}
      <form onSubmit={(event) => void sendText(event)} className="flex shrink-0 items-end gap-2 border-t border-white/5 bg-slate-900/95 p-2">
        <input
          ref={fileRef}
          type="file"
          accept={[...IMAGE_TYPES, ...VIDEO_TYPES, 'video/*'].join(',')}
          data-testid="private-file-input"
          className="hidden"
          onChange={(event) => {
            pickerDone(token);
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void sendPicture(file);
          }}
        />
        <button
          type="button"
          onClick={() => {
            // On Android the picker sends the app to the background: keep the key until the picture is back.
            pickerOpening(token);
            fileRef.current?.click();
          }}
          disabled={sending}
          aria-label="Send an encrypted photo or video"
          data-testid="private-pick"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 disabled:opacity-50"
        >
          <ImageIcon size={18} />
        </button>
        <button
          type="button"
          onClick={() => setCamera('photo')}
          disabled={sending}
          aria-label="Take an encrypted photo or video"
          data-testid="private-camera-open"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 disabled:opacity-50"
        >
          <Camera size={18} />
        </button>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !isImeKey(event) && !Capacitor.isNativePlatform()) {
              event.preventDefault();
              void sendText(event);
            }
          }}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData?.files ?? []);
            if (!files.length) return;
            // A pasted picture goes the same encrypted way as a picked one.
            event.preventDefault();
            void sendFiles(files);
          }}
          ref={inputRef}
          rows={1}
          placeholder={editing ? 'Edit your message' : 'Private message'}
          aria-label={editing ? 'Edit your message' : 'Private message'}
          data-testid="private-input"
          className="glass-input max-h-32 min-h-11 min-w-0 flex-1 resize-none text-base sm:text-sm"
        />
        <button
          type="submit"
          disabled={sending || !text.trim()}
          aria-label={editing ? 'Save the edit' : 'Send'}
          data-testid={editing ? 'private-edit-save' : 'private-send'}
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-white disabled:opacity-50 ${editing ? 'bg-amber-600' : 'bg-rose-600'}`}
        >
          {sending ? <Loader2 size={17} className="animate-spin" /> : editing ? <Check size={18} /> : <SendHorizontal size={17} />}
        </button>
      </form>

      {sheetMessage && sheetId !== null && sheetText !== null && (
        <PrivateMessageSheet
          mine={new Set((marks[sheetId]?.reactions ?? []).filter((r) => r.own).map((r) => r.emoji))}
          editedLabel={sheetEdit ? `Edited at ${timeLabel(sheetEdit.at)}` : undefined}
          onReact={(emoji) => void react(sheetId, emoji)}
          onEdit={sheetOwn ? () => startEdit(sheetId, sheetEdit ? sheetEdit.text : sheetText) : undefined}
          onDelete={sheetOwn ? () => void remove(sheetMessage) : undefined}
          onClose={() => setSelected(null)}
        />
      )}
      {gallery && (
        <PrivateGallery
          token={token}
          vault={vault}
          messages={messages}
          myActorIds={myActorIds}
          initialBlob={gallery.blob}
          onClose={() => setGallery(null)}
          onChanged={onChanged}
          onDelete={removeById}
        />
      )}
      {beloved && belovedAllowed && <BelovedPage token={token} vault={vault} onClose={() => setBeloved(false)} onAddToHome={onAddBelovedToHome} />}
      {camera === 'photo' && (
        <PrivateCamera
          onClose={() => setCamera(null)}
          onVideo={() => setCamera('video')}
          onCapture={async (jpeg, takenAt) => {
            setSending(true);
            try {
              await sendImageBytes(jpeg, 'image/jpeg', undefined, takenAt);
            } finally {
              setSending(false);
            }
          }}
        />
      )}
      {camera === 'video' && (
        <PrivateVideoRecorder
          onClose={() => setCamera(null)}
          onPhoto={() => setCamera('photo')}
          onSend={async (clip, progress) => {
            setSending(true);
            try {
              await sendVideo(clip.blob, clip.mime, { takenAt: clip.takenAt, duration: clip.duration }, progress);
            } finally {
              setSending(false);
            }
          }}
        />
      )}
      {securityOpen && (
        <PrivateChatSecurity
          token={token}
          partner={partner}
          partnerName={partnerName}
          vault={vault}
          onChanged={() => void queryClient.invalidateQueries({ queryKey: ['private-chat-status'] })}
          onClose={() => setSecurityOpen(false)}
        />
      )}
      {watching && (
        <PrivateVideoPlayer
          token={token}
          vault={vault}
          video={watching.video}
          title={watching.video.name || 'Video'}
          onClose={() => setWatching(null)}
          mine={new Set((typeof watching.message.id === 'number' ? marks[watching.message.id]?.reactions ?? [] : []).filter((r) => r.own).map((r) => r.emoji))}
          onReact={
            typeof watching.message.id === 'number'
              ? (emoji) => {
                  const id = watching.message.id as number;
                  setWatching(null);
                  void react(id, emoji);
                }
              : undefined
          }
          onDelete={
            watching.own
              ? () => {
                  const target = watching.message;
                  setWatching(null);
                  void remove(target);
                }
              : undefined
          }
        />
      )}
      {viewing && (
        <PictureViewer
          url={viewing.url}
          name={viewing.name}
          onClose={() => setViewing(null)}
          mine={new Set((typeof viewing.message.id === 'number' ? marks[viewing.message.id]?.reactions ?? [] : []).filter((r) => r.own).map((r) => r.emoji))}
          onReact={
            typeof viewing.message.id === 'number'
              ? (emoji) => {
                  const id = viewing.message.id as number;
                  setViewing(null);
                  void react(id, emoji);
                }
              : undefined
          }
          onDelete={
            viewing.own
              ? () => {
                  const target = viewing.message;
                  setViewing(null);
                  void remove(target);
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

/** The encrypted blobs a message is made of: a picture's one, or a clip's pieces and its poster. */
function blobsOf(payload: Opened | undefined): string[] {
  if (!payload || payload === 'unreadable') return [];
  if (payload.t === 'image') return [payload.blob];
  if (payload.t === 'video') return [...payload.chunks, payload.poster];
  return [];
}

function Receipt({ read }: { read: boolean }) {
  return read ? (
    <span className="inline-flex items-center gap-0.5 font-semibold text-rose-200" data-testid="private-receipt">
      <CheckCheck size={12} /> Read
    </span>
  ) : (
    <span className="inline-flex items-center gap-0.5" data-testid="private-receipt">
      <Check size={12} /> Sent
    </span>
  );
}

/** One encrypted picture: fetched, decrypted in memory, shown from an object URL the vault revokes on lock. */
function PrivatePicture({
  token,
  payload,
  vault,
  onOpen,
}: {
  token: string;
  payload: Extract<PrivatePayload, { t: 'image' }>;
  vault: PrivateVault;
  onOpen: (url: string) => void;
}) {
  const { url, failed } = usePrivatePicture(token, payload.blob, payload.mime, vault);

  if (failed) return <span className="italic opacity-80">This picture could not be opened.</span>;
  if (!url) {
    return (
      <span className="flex h-40 w-56 max-w-full items-center justify-center rounded-2xl bg-black/20">
        <Loader2 size={18} className="animate-spin" />
      </span>
    );
  }
  return (
    <button type="button" onClick={() => onOpen(url)} className="block cursor-zoom-in text-left" aria-label="View picture">
      <img
        src={url}
        alt={payload.caption || 'Encrypted picture'}
        {...noSave}
        className="pointer-events-none max-h-72 w-60 max-w-full select-none rounded-2xl object-cover [-webkit-touch-callout:none] [-webkit-user-drag:none]"
        data-testid="private-picture"
      />
      {payload.caption && <span className="mt-1 block">{payload.caption}</span>}
    </button>
  );
}

/** Full-screen view of a picture, inside the chat only (no download, share or new tab). */
function PictureViewer({
  url,
  name,
  onClose,
  onDelete,
  onReact,
  mine,
}: {
  url: string;
  name: string;
  onClose: () => void;
  onDelete?: () => void;
  /** React to the picture (either parent's); the viewer closes so its bubble can play the animation. */
  onReact?: (emoji: string) => void;
  mine: ReadonlySet<string>;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/95 p-3" role="dialog" aria-modal="true" aria-label={name} {...noSave}>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close picture"
        className="absolute right-3 top-[max(0.75rem,var(--safe-top))] z-10 flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-white"
      >
        <X size={20} />
      </button>
      {(onReact || onDelete) && (
        <div className="absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-2 bg-gradient-to-t from-black/80 to-transparent px-3 pt-8 pb-[max(0.75rem,var(--safe-bottom))]">
          {onReact && (
            <div className="w-full max-w-sm">
              <ReactionRow mine={mine} onReact={onReact} tone="viewer" />
            </div>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              className="flex min-h-11 items-center gap-1.5 rounded-full border border-rose-400/30 bg-rose-500/20 px-4 text-xs font-semibold text-rose-100"
            >
              <Trash2 size={13} aria-hidden /> Delete for both of you
            </button>
          )}
        </div>
      )}
      <img
        src={url}
        alt={name}
        {...noSave}
        className="pointer-events-none max-h-full max-w-full select-none object-contain [-webkit-touch-callout:none] [-webkit-user-drag:none]"
      />
    </div>
  );
}

/** Whether this page is on screen (reactions wait to play until it is). */
function usePageVisible(): boolean {
  return useSyncExternalStore(
    (fn) => {
      document.addEventListener('visibilitychange', fn);
      return () => document.removeEventListener('visibilitychange', fn);
    },
    () => document.visibilityState !== 'hidden',
    () => true,
  );
}

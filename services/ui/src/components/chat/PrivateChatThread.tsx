import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type FormEvent } from 'react';
import { App as CapacitorApp } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { BellRing, Camera, Heart, Check, CheckCheck, Fingerprint, Images, MessageSquare, ImageIcon, KeyRound, Loader2, Lock, SendHorizontal, ShieldAlert, Trash2, X } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { IMAGE_TYPES, PrivateVault, isSealedMessage, type PrivatePayload } from '../../lib/privateChatCrypto';
import { chatLeft, chatShown, lockNow, pickerDone, pickerOpening, vaultFor } from '../../lib/privateChatSession';
import { sha256Hex } from '../../lib/galleryImport';
import { secureWindowOff, secureWindowOn, type SecureState } from '../../plugins/secureWindow';
import { deviceUnlockEnabled, deviceUnlockError, deviceUnlockPlugin } from '../../plugins/deviceUnlock';
import { keyFromDevice } from '../../lib/privateChatDeviceUnlock';
import PrivateChatSecurity from './PrivateChatSecurity';
import PrivateCamera from './PrivateCamera';
import PrivateGallery from './PrivateGallery';
import BelovedPage from './beloved/BelovedPage';
import { Lily } from './beloved/BelovedArt';
import { useLoveIndicator } from './beloved/useLoveIndicator';
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
  timeLabel,
  type TalkConversation,
  type TalkMessage,
} from './chatModel';
import { canNudge, errorText, threadMessages, type PrivateChatInfo } from './privateChatModel';
import { isImeKey } from '../../lib/keyboard';

interface PrivateChatThreadProps {
  conversation: TalkConversation;
  /** The chat's key parameters; undefined while they load or when the server refuses them. */
  info?: PrivateChatInfo;
  /** Why `info` is missing (the server's reason), if it is. */
  infoError?: string;
  /** The raw Talk messages, oldest first: ciphertext until unlocked. */
  messages: TalkMessage[];
  myActorIds: ReadonlySet<string>;
  /** A key made moments ago for a chat just created, so its creator is not asked again at once. */
  adoptKey?: CryptoKey;
  /** The adopted key is held now: the panel forgets it, so coming back asks for the password. */
  onKeyAdopted?: () => void;
  onUnlockedChange: (unlocked: boolean) => void;
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
  useEffect(() => () => onUnlockedChange(false), [onUnlockedChange]);

  // Dropped decryptions on lock: nothing readable outlives the key.
  useEffect(() => {
    onUnlockedChange(unlocked);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- wiping what was decrypted when the key goes
    if (!unlocked) setOpened({});
  }, [unlocked, onUnlockedChange]);

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
      token={token}
      conversation={conversation}
      messages={messages}
      opened={opened}
      myActorIds={myActorIds}
      vault={vault}
      secure={secure}
      belovedAllowed={belovedAllowed}
      onChanged={onChanged}
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
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 py-6" data-testid="private-lock">
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
  token: string;
  conversation: TalkConversation;
  messages: TalkMessage[];
  opened: Record<string, Opened>;
  myActorIds: ReadonlySet<string>;
  vault: PrivateVault;
  secure: SecureState | 'pending';
  /** Whether the signed-in parent may open Beloved here (one father and one mother). */
  belovedAllowed: boolean;
  onChanged: () => void;
}

function UnlockedChat({ partnerName, token, conversation, messages, opened, myActorIds, vault, secure, belovedAllowed, onChanged }: UnlockedChatProps) {
  const queryClient = useQueryClient();
  const [securityOpen, setSecurityOpen] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  // The gallery, opened on one picture (a comment's) or on the grid.
  const [gallery, setGallery] = useState<{ blob?: string } | null>(null);
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
  const [nudging, setNudging] = useState(false);
  const [nudgeSent, setNudgeSent] = useState(false);
  const { trigger } = useHaptics();
  const [selected, setSelected] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ url: string; name: string; message?: TalkMessage } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);

  // Only what was said in the chat: a picture added straight to the gallery is never part of the thread.
  const visible = threadMessages(messages, opened);
  const mine = visible.filter((m) => !isSystemMessage(m) && isMine(m, myActorIds));
  const myLatest = mine[mine.length - 1];
  const nudgeable = canNudge(visible.filter((m) => !isSystemMessage(m)), myActorIds, conversation.last_common_read);

  const lastId = visible[visible.length - 1]?.id;
  const openedCount = Object.keys(opened).length;
  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [lastId, openedCount]);

  const post = async (payload: PrivatePayload) => {
    const message = await vault.seal(payload);
    const res = await api.sendTalkMessage({ token, message });
    if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Message failed to send');
  };

  const sendText = async (event: FormEvent) => {
    event.preventDefault();
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      await post({ t: 'text', text: body });
      setText('');
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
      await post({ t: 'image', blob, mime, ...(name ? { name } : {}), sent_at: Math.floor(Date.now() / 1000), ...(takenAt ? { taken_at: takenAt } : {}), sha256 });
      onChanged();
    } catch (error) {
      if (blob) void api.deletePrivateBlob(token, blob).catch(() => {});
      throw error;
    }
  };

  /** A photo from the picker, a paste or a drop. Anything that is not a photo is refused. */
  const sendPicture = async (file: File) => {
    if (!IMAGE_TYPES.has(file.type)) {
      toast.error('Only photos can be sent in a private chat');
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

  /** Delete a message for both of you, and the picture's encrypted blob when it is one. */
  const removeById = async (messageId: number, blob?: string) => {
    setSelected(null);
    try {
      const res = await api.deleteTalkMessage({ token, message_id: messageId });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Could not delete the message');
      if (blob) await api.deletePrivateBlob(token, blob);
      onChanged();
    } catch (error) {
      toast.error(errorText(error, 'Could not delete the message'));
    }
  };

  const remove = async (message: TalkMessage) => {
    if (typeof message.id !== 'number') return;
    const payload = opened[String(message.id)];
    await removeById(message.id, payload && payload !== 'unreadable' && payload.t === 'image' ? payload.blob : undefined);
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

      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3 sm:px-4" data-testid="private-feed" role="log" aria-label="Messages">
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
          const picture = payload !== undefined && payload !== 'unreadable' && payload.t === 'image';
          const comment = payload !== undefined && payload !== 'unreadable' && payload.t === 'comment';
          // A text bubble of yours is tapped for Delete; a picture opens the viewer and a comment the gallery, which have Delete.
          const selectable = own && !picture && !comment && typeof message.id === 'number';
          return (
            <div key={key}>
              {separator}
              <div className={`mb-2 flex flex-col ${own ? 'items-end' : 'items-start'}`}>
                <div
                  role={selectable ? 'button' : undefined}
                  tabIndex={selectable ? 0 : undefined}
                  onClick={() => selectable && setSelected(isSelected ? null : key)}
                  onKeyDown={(event) => {
                    if (selectable && (event.key === 'Enter' || event.key === ' ')) {
                      event.preventDefault();
                      setSelected(isSelected ? null : key);
                    }
                  }}
                  className={`max-w-[82%] select-text whitespace-pre-wrap break-words rounded-3xl px-3.5 py-2 text-left text-[15px] leading-snug sm:max-w-[70%] ${
                    own
                      ? 'rounded-br-md bg-gradient-to-br from-rose-600 to-purple-600 text-white'
                      : 'rounded-bl-md border border-white/[0.06] bg-slate-800/90 text-slate-100'
                  } ${isSelected ? 'ring-2 ring-rose-300/60' : ''}`}
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
                    payload.text
                  ) : payload.t === 'comment' ? (
                    <button
                      type="button"
                      onClick={() => setGallery({ blob: payload.blob })}
                      className="block text-left"
                      data-testid="private-comment-bubble"
                    >
                      <span className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold opacity-80">
                        <MessageSquare size={11} aria-hidden /> On a photo
                      </span>
                      {payload.text}
                    </button>
                  ) : payload.t === 'image' ? (
                    <PrivatePicture
                      token={token}
                      payload={payload}
                      vault={vault}
                      onOpen={(url) => setViewing({ url, name: payload.name || 'Picture', message: own ? message : undefined })}
                    />
                  ) : null}
                </div>
                {isSelected && (
                  <button
                    type="button"
                    onClick={() => void remove(message)}
                    className="mt-1 flex min-h-11 items-center gap-1.5 rounded-full border border-rose-400/30 bg-rose-500/10 px-3 text-xs font-semibold text-rose-200"
                  >
                    <Trash2 size={13} aria-hidden /> Delete for both of you
                  </button>
                )}
                <span className="mt-0.5 flex items-center gap-1.5 px-2 text-[10px] text-slate-500">
                  {message.timestamp ? timeLabel(message.timestamp) : ''}
                  {own && message === myLatest && <Receipt read={receiptFor(message, conversation.last_common_read) === 'read'} />}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <form onSubmit={(event) => void sendText(event)} className="flex shrink-0 items-end gap-2 border-t border-white/5 bg-slate-900/95 p-2">
        <input
          ref={fileRef}
          type="file"
          accept={[...IMAGE_TYPES].join(',')}
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
          aria-label="Send an encrypted photo"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 disabled:opacity-50"
        >
          <ImageIcon size={18} />
        </button>
        <button
          type="button"
          onClick={() => setCameraOpen(true)}
          disabled={sending}
          aria-label="Take an encrypted photo"
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
          rows={1}
          placeholder="Private message"
          aria-label="Private message"
          data-testid="private-input"
          className="glass-input max-h-32 min-h-11 min-w-0 flex-1 resize-none text-base sm:text-sm"
        />
        <button
          type="submit"
          disabled={sending || !text.trim()}
          aria-label="Send"
          data-testid="private-send"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-rose-600 text-white disabled:opacity-50"
        >
          {sending ? <Loader2 size={17} className="animate-spin" /> : <SendHorizontal size={17} />}
        </button>
      </form>

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
      {beloved && belovedAllowed && <BelovedPage token={token} vault={vault} onClose={() => setBeloved(false)} />}
      {cameraOpen && (
        <PrivateCamera
          onClose={() => setCameraOpen(false)}
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
      {securityOpen && (
        <PrivateChatSecurity
          token={token}
          partnerName={partnerName}
          onChanged={() => void queryClient.invalidateQueries({ queryKey: ['private-chat-status'] })}
          onClose={() => setSecurityOpen(false)}
        />
      )}
      {viewing && (
        <PictureViewer
          url={viewing.url}
          name={viewing.name}
          onClose={() => setViewing(null)}
          onDelete={
            viewing.message
              ? () => {
                  const target = viewing.message as TalkMessage;
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
function PictureViewer({ url, name, onClose, onDelete }: { url: string; name: string; onClose: () => void; onDelete?: () => void }) {
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
        className="absolute right-3 top-[max(0.75rem,env(safe-area-inset-top))] z-10 flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-white"
      >
        <X size={20} />
      </button>
      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          className="absolute bottom-[max(0.75rem,env(safe-area-inset-bottom))] left-1/2 z-10 flex min-h-11 -translate-x-1/2 items-center gap-1.5 rounded-full border border-rose-400/30 bg-rose-500/20 px-4 text-xs font-semibold text-rose-100"
        >
          <Trash2 size={13} aria-hidden /> Delete for both of you
        </button>
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

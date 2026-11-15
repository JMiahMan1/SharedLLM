import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type FormEvent } from 'react';
import { App as CapacitorApp } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { BellRing, Check, CheckCheck, ImageIcon, KeyRound, Loader2, Lock, SendHorizontal, ShieldAlert, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { IMAGE_TYPES, PrivateVault, isSealedMessage, type PrivatePayload } from '../../lib/privateChatCrypto';
import { secureWindowOff, secureWindowOn, type SecureState } from '../../plugins/secureWindow';
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
import { canNudge, errorText, type PrivateChatInfo } from './privateChatModel';

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

/** Stop the browser offering to save, share, drag or open a picture elsewhere. */
const noSave = {
  draggable: false,
  onContextMenu: (event: { preventDefault: () => void }) => event.preventDefault(),
  onDragStart: (event: { preventDefault: () => void }) => event.preventDefault(),
};

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
  // One vault per mounted chat (the panel remounts this per conversation).
  const [vault] = useState(() => new PrivateVault());
  const subscribe = useCallback((fn: () => void) => vault.subscribe(fn), [vault]);
  const unlocked = useSyncExternalStore(subscribe, () => vault.unlocked);
  const [opened, setOpened] = useState<Record<string, Opened>>({});
  const [secure, setSecure] = useState<SecureState | 'pending'>('pending');
  const token = conversation.token;

  useEffect(() => {
    if (!adoptKey) return;
    vault.adopt(adoptKey);
    onKeyAdopted?.();
  }, [adoptKey, vault, onKeyAdopted]);

  // Lock on leaving: this screen going away, the tab or app going to the background.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') vault.lock();
    };
    document.addEventListener('visibilitychange', onVisibility);
    let pause: { remove: () => Promise<void> } | undefined;
    if (Capacitor.isNativePlatform()) {
      void CapacitorApp.addListener('pause', () => vault.lock())
        .then((handle) => {
          pause = handle;
        })
        .catch(() => {});
    }
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      void pause?.remove();
      vault.lock();
    };
  }, [vault]);

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
    return <LockScreen info={info} infoError={infoError} vault={vault} partner={info?.partner_name} />;
  }
  return (
    <UnlockedChat
      token={token}
      conversation={conversation}
      messages={messages}
      opened={opened}
      myActorIds={myActorIds}
      vault={vault}
      secure={secure}
      onChanged={onChanged}
    />
  );
}

function LockScreen({ info, infoError, vault, partner }: { info?: PrivateChatInfo; infoError?: string; vault: PrivateVault; partner?: string }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState('');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!info || !password || busy) return;
    setBusy(true);
    setWrong('');
    try {
      const ok = await vault.unlock(password, { salt: info.kdf.salt, iterations: info.kdf.iterations }, info.verifier);
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
  token: string;
  conversation: TalkConversation;
  messages: TalkMessage[];
  opened: Record<string, Opened>;
  myActorIds: ReadonlySet<string>;
  vault: PrivateVault;
  secure: SecureState | 'pending';
  onChanged: () => void;
}

function UnlockedChat({ token, conversation, messages, opened, myActorIds, vault, secure, onChanged }: UnlockedChatProps) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [nudging, setNudging] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ url: string; name: string; message?: TalkMessage } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);

  const visible = messages.filter((m) => !m.deleted);
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
    let blob = '';
    try {
      const sealed = await vault.encrypt(new Uint8Array(await file.arrayBuffer()));
      blob = (await api.uploadPrivateBlob(token, sealed)).id;
      await post({ t: 'image', blob, mime: file.type, name: file.name });
      onChanged();
    } catch (error) {
      if (blob) void api.deletePrivateBlob(token, blob).catch(() => {});
      toast.error(errorText(error, 'Could not send the picture'));
    } finally {
      setSending(false);
    }
  };

  const remove = async (message: TalkMessage) => {
    if (typeof message.id !== 'number') return;
    setSelected(null);
    try {
      const res = await api.deleteTalkMessage({ token, message_id: message.id });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Could not delete the message');
      const payload = opened[String(message.id)];
      if (payload && payload !== 'unreadable' && payload.t === 'image') await api.deletePrivateBlob(token, payload.blob);
      onChanged();
    } catch (error) {
      toast.error(errorText(error, 'Could not delete the message'));
    }
  };

  const nudge = async () => {
    setNudging(true);
    try {
      await api.nudgePrivateChat(token);
      toast.success('Nudged');
    } catch (error) {
      toast.error(errorText(error, 'Could not nudge'));
    } finally {
      setNudging(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="private-chat">
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
            <BellRing size={14} aria-hidden /> Nudge
          </button>
        )}
        <button
          type="button"
          onClick={() => vault.lock()}
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
          // A text bubble of yours is tapped for Delete; a picture opens the viewer, which has Delete.
          const selectable = own && !picture && typeof message.id === 'number';
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
                  ) : (
                    <PrivatePicture
                      token={token}
                      payload={payload}
                      vault={vault}
                      onOpen={(url) => setViewing({ url, name: payload.name || 'Picture', message: own ? message : undefined })}
                    />
                  )}
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
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void sendPicture(file);
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={sending}
          aria-label="Send an encrypted photo"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 disabled:opacity-50"
        >
          <ImageIcon size={18} />
        </button>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !Capacitor.isNativePlatform()) {
              event.preventDefault();
              void sendText(event);
            }
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
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    void api
      .getPrivateBlob(token, payload.blob)
      .then((bytes) => vault.pictureUrl(bytes, payload.mime))
      .then((made) => {
        if (live) setUrl(made);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [token, payload.blob, payload.mime, vault]);

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

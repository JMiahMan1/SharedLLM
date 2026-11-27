import { useRef, useState } from 'react';
import { Camera, MapPin, Send, WifiOff } from 'lucide-react';
import toast from 'react-hot-toast';
import FamilyMesh, { type MeshMessage } from '../../plugins/familyMesh';
import type { OfflineChat } from './useOfflineChat';
import { errorText } from '../../lib/errorText';
import { deliveryLine, makeThumbnail, mapLink } from '../../lib/offlineChat';

/** The line at the top of the inbox while there is no internet. */
export function OfflineChatNotice({ chat }: { chat: OfflineChat }) {
  if (!chat.enabled || !chat.diag) return null;
  return (
    <div className="mb-3 space-y-1 rounded-2xl border border-sky-400/20 bg-sky-500/10 px-3 py-2" role="status" data-testid="chat-offline">
      <p className="flex items-center gap-2 text-xs font-semibold text-sky-100">
        <WifiOff size={14} aria-hidden /> No internet: chats go through family phones nearby
      </p>
      <p className="text-xs text-slate-300">
        Open any chat and write as usual. Only its members can read it, and it is posted when your phone is online again.
      </p>
      {!chat.diag.connectPermission && (
        <p className="text-xs text-amber-200" data-testid="offline-chat-permission">
          Allow Nearby devices (Settings, Nearby &amp; offline) to pass messages between phones.
        </p>
      )}
      {chat.rooms.length === 0 && (
        <p className="text-xs text-amber-200" data-testid="offline-chat-no-rooms">
          Your chats are not ready to use offline yet. Open Jarvis once with the internet on.
        </p>
      )}
    </div>
  );
}

interface MessagesProps {
  messages: MeshMessage[];
  /** The signed-in person (their own messages are drawn on the right). */
  me: string;
  nameOf?: (username: string) => string | undefined;
}

/** A room's offline messages, under its history, each with where it is. */
export function OfflineRoomMessages({ messages, me, nameOf }: MessagesProps) {
  if (messages.length === 0) return null;
  const name = (u: string) => (u === me ? 'You' : nameOf?.(u) ?? u);
  return (
    <ul
      className="max-h-[40%] shrink-0 space-y-2 overflow-y-auto border-t border-white/5 px-3 py-2"
      aria-label="Offline messages"
      data-testid="offline-room-messages"
    >
      {messages.map((m) => (
        <li key={m.id} className={`flex ${m.mine ? 'justify-end' : 'justify-start'}`}>
          <div className={`min-w-0 max-w-[85%] rounded-xl px-3 py-2 text-sm ${m.mine ? 'bg-purple-600/30' : 'bg-white/10'}`}>
            <p className="text-[11px] font-semibold text-slate-300">{name(m.from)}</p>
            {m.t === 'text' && <p className="whitespace-pre-wrap break-words text-white">{m.text}</p>}
            {m.t === 'pin' && m.lat != null && m.lon != null && (
              <a href={mapLink(m.lat, m.lon)} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-1 break-all text-sky-200 underline">
                <MapPin size={13} /> {m.label || 'A place'}
              </a>
            )}
            {m.t === 'thumb' && m.data && (
              <img src={`data:image/jpeg;base64,${m.data}`} alt={m.caption || 'Photo preview'} className="max-h-40 rounded-lg" />
            )}
            <p className="mt-1 text-[10px] text-slate-400">{deliveryLine(m)}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

interface ComposerProps {
  /** The room's token, as the server knows it. */
  room: string;
  /** This phone holds the room's key (fetched while online). */
  ready: boolean;
  onSent?: () => void;
}

function here(): Promise<{ lat: number; lon: number } | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(null);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 120_000 },
    );
  });
}

/** The composer of any room while there is no internet: text, where I am, a small photo preview. */
export function OfflineRoomComposer({ room, ready, onSent }: ComposerProps) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  // Unsent words belong to the room they were typed in.
  const [shownRoom, setShownRoom] = useState(room);
  if (shownRoom !== room) {
    setShownRoom(room);
    setText('');
  }

  const send = async (kind: 'text' | 'pin' | 'thumb', fields: Record<string, unknown>) => {
    setBusy(true);
    try {
      await FamilyMesh.send({ room, kind, fields });
      onSent?.();
      return true;
    } catch (e) {
      toast.error(errorText(e, 'Could not keep that message'));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onPhoto = async (file: File | undefined) => {
    if (!file) return;
    const data = await makeThumbnail(file).catch(() => null);
    if (!data) {
      toast.error('That photo could not be made small enough to send offline.');
      return;
    }
    await send('thumb', { data });
  };

  const onPlace = async () => {
    const at = await here();
    if (!at) {
      toast.error('Could not tell where you are');
      return;
    }
    await send('pin', { lat: at.lat, lon: at.lon, label: 'Where I am' });
  };

  if (!ready) {
    return (
      <p className="shrink-0 border-t border-white/5 px-3 py-3 text-xs text-amber-200" data-testid="offline-room-not-ready">
        This chat is not ready to use offline yet. Open Jarvis once with the internet on.
      </p>
    );
  }

  return (
    <form
      className="flex shrink-0 flex-wrap items-center gap-2 border-t border-white/5 px-3 py-2"
      data-testid="offline-room-composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim()) return;
        void send('text', { text }).then((ok) => {
          if (ok) setText('');
        });
      }}
    >
      <p className="w-full text-[11px] text-sky-200">No internet: this goes to family phones nearby.</p>
      <label htmlFor="offline-room-text" className="sr-only">Offline message</label>
      <input
        id="offline-room-text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={2000}
        placeholder="Message"
        className="glass-input min-h-11 min-w-0 flex-1 px-3 text-base sm:text-sm"
      />
      <button type="submit" disabled={busy || !text.trim()} aria-label="Send offline"
        className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-purple-600 text-white disabled:opacity-50">
        <Send size={15} />
      </button>
      <button type="button" disabled={busy} aria-label="Share my location offline"
        onClick={() => void onPlace()}
        className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-white/10 text-sky-200 disabled:opacity-50">
        <MapPin size={15} />
      </button>
      <button type="button" disabled={busy} aria-label="Send a photo preview offline"
        onClick={() => fileRef.current?.click()}
        className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-white/10 text-sky-200 disabled:opacity-50">
        <Camera size={15} />
      </button>
      <input ref={fileRef} type="file" accept="image/*" className="hidden" data-testid="offline-room-photo"
        onChange={(e) => { void onPhoto(e.target.files?.[0]); e.target.value = ''; }} />
    </form>
  );
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Camera, MapPin, MessageCircle, Send } from 'lucide-react';
import toast from 'react-hot-toast';
import FamilyMesh, { type MeshDiagnostics, type MeshMessage } from '../../plugins/familyMesh';
import { errorText } from '../../lib/errorText';
import { deliveryLine, makeThumbnail, mapLink } from '../../lib/offlineChat';

interface Props {
  /** The signed-in person (their own messages are drawn on the right). */
  me: string;
  /** The family chat the messages go to once online (Chat's "open on launch" conversation). */
  room: string | null | undefined;
  /** Where this phone is now, for "Share my location". */
  position?: { lat: number; lon: number } | null;
  /** A family member's name by username. */
  nameOf?: (username: string) => string | undefined;
}

/**
 * The offline family chat (Android app; in Chat, on when Settings > Nearby &
 * offline says so): messages, a place or a small
 * photo preview to the family's phones nearby over Bluetooth, with no Wi-Fi or
 * data. Sealed with the household's chat key, passed from phone to phone, and
 * posted to the family chat once, when the sender is online again.
 */
export default function OfflineChatCard({ me, room, position, nameOf }: Props) {
  const android = Capacitor.getPlatform() === 'android';
  const [diag, setDiag] = useState<MeshDiagnostics | null>(null);
  const [messages, setMessages] = useState<MeshMessage[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const reload = useCallback(() => {
    Promise.all([FamilyMesh.getDiagnostics(), FamilyMesh.list()])
      .then(([d, l]) => {
        setDiag(d);
        setMessages(l.messages);
      })
      .catch(() => setDiag(null));
  }, []);

  useEffect(() => {
    if (!android) return undefined;
    reload();
    let remove: (() => Promise<void>) | undefined;
    void FamilyMesh.addListener('change', reload).then((h) => { remove = h.remove; });
    return () => { void remove?.(); };
  }, [android, reload]);

  useEffect(() => {
    if (android && room) void FamilyMesh.setRoom({ room }).catch(() => undefined);
  }, [android, room]);

  // Turned on and off in Settings > Nearby & offline.
  if (!android || !diag || !diag.enabled) return null;

  const send = async (kind: 'text' | 'pin' | 'thumb', fields: Record<string, unknown>) => {
    setBusy(true);
    try {
      await FamilyMesh.send({ kind, fields });
      reload();
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

  const name = (u: string) => (u === me ? 'You' : nameOf?.(u) ?? u);

  return (
    <div className="glass-panel space-y-3 rounded-2xl border border-white/5 p-4" data-testid="offline-chat">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <MessageCircle size={15} className="text-sky-300" /> Offline family chat
        </h3>
      </div>
      <p className="text-xs text-slate-400">
        With no Wi-Fi or signal, messages reach family phones nearby over Bluetooth and go to the family chat once someone is online.
      </p>

      {!diag.connectPermission && (
        <p className="text-xs text-amber-200" data-testid="offline-chat-permission">Allow Nearby devices (Settings, Nearby &amp; offline) to pass messages between phones.</p>
      )}
      {!diag.room && (
        <p className="text-xs text-amber-200" data-testid="offline-chat-room">
          Choose the chat to open on launch in Chat first: offline messages go there when back online.
        </p>
      )}

      <>
          <ul className="max-h-72 space-y-2 overflow-y-auto" aria-label="Offline messages" data-testid="offline-chat-messages">
            {messages.length === 0 && <li className="text-xs text-slate-500">No offline messages.</li>}
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

          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!text.trim()) return;
              void send('text', { text }).then((ok) => { if (ok) setText(''); });
            }}
          >
            <label htmlFor="offline-chat-text" className="sr-only">Offline message</label>
            <input
              id="offline-chat-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={2000}
              placeholder="Message nearby family"
              className="glass-input min-h-11 min-w-0 flex-1 px-3 text-sm"
            />
            <button type="submit" disabled={busy || !text.trim()} aria-label="Send offline"
              className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-purple-600 text-white disabled:opacity-50">
              <Send size={15} />
            </button>
            <button type="button" disabled={busy || !position} aria-label="Share my location offline"
              onClick={() => position && void send('pin', { lat: position.lat, lon: position.lon, label: 'Where I am' })}
              className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-white/10 text-sky-200 disabled:opacity-50">
              <MapPin size={15} />
            </button>
            <button type="button" disabled={busy} aria-label="Send a photo preview offline"
              onClick={() => fileRef.current?.click()}
              className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-white/10 text-sky-200 disabled:opacity-50">
              <Camera size={15} />
            </button>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" data-testid="offline-chat-photo"
              onChange={(e) => { void onPhoto(e.target.files?.[0]); e.target.value = ''; }} />
          </form>
      </>
    </div>
  );
}

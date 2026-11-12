import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import toast from 'react-hot-toast';
import { ArrowLeft, Bluetooth, CheckCircle2, Loader2, MapPinned, Play, RotateCcw, Square, Trash2 } from 'lucide-react';
import BleBeacon, { type BeaconStatus } from '../../plugins/bleBeacon';
import { failureText, getEntries } from '../../lib/discoveredDevices';
import { getPhones } from '../../lib/phones';
import {
  PASS_SECONDS,
  beaconIsLive,
  beaconTrackerId,
  cancelMapSession,
  clearMappedRoom,
  clearWholeMap,
  clock,
  confusedPairs,
  getMapSession,
  getRoomMap,
  percent,
  phoneTrackers,
  roomRows,
  startErrorText,
  startMapSession,
  validTagId,
  type RoomRow,
  type SessionStatus,
  type TrackerOption,
} from '../../lib/roomMap';
import { holdScreenOn, type AwakeHold } from '../../lib/keepAwake';
import { relativeTime } from '../../lib/wanderTrips';
import { confirmDialog } from '../ui/confirmDialog';

const MAP_KEY = ['room-map'];
const button = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-white/10 px-3 text-sm text-slate-200 hover:bg-white/5 disabled:opacity-50';
const primary = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-4 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50';
const field = 'min-h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm text-white placeholder:text-slate-500';

const THIS_PHONE = 'this-phone';
const TAG = 'tag';

const STATE_TEXT: Record<RoomRow['state'], string> = { not_mapped: 'Not mapped', mapped: 'Mapped', stale: 'Stale' };
const STATE_TONE: Record<RoomRow['state'], string> = {
  not_mapped: 'bg-slate-500/15 text-slate-300',
  mapped: 'bg-emerald-500/15 text-emerald-300',
  stale: 'bg-amber-500/15 text-amber-300',
};

type Phase = 'ready' | 'starting' | 'recording' | 'done' | 'failed';

/** One room being recorded: instructions, Start, the live countdown, then the result. */
function Recorder({ room, tracker, thisPhone, onBack, onFinished }: {
  room: RoomRow;
  tracker: string;
  thisPhone: boolean;
  onBack: () => void;
  onFinished: () => void;
}) {
  const [duration, setDuration] = useState(PASS_SECONDS[0]);
  const [phase, setPhase] = useState<Phase>('ready');
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [awake, setAwake] = useState<AwakeHold | null | undefined>(undefined);
  const [passes, setPasses] = useState(0);
  const session = useRef<string | null>(null);
  const hold = useRef<AwakeHold | null>(null);

  const letSleep = useCallback(() => {
    const h = hold.current;
    hold.current = null;
    if (h) void h.release().catch(() => undefined);
  }, []);

  // Leaving mid-recording stops the session rather than leaving it running on the hub.
  useEffect(() => () => {
    if (session.current) void cancelMapSession(session.current).catch(() => undefined);
    letSleep();
  }, [letSleep]);

  useEffect(() => {
    if (phase !== 'recording' || !session.current) return undefined;
    const id = session.current;
    let alive = true;
    const poll = async () => {
      try {
        const s = await getMapSession(id);
        if (!alive) return;
        setStatus(s);
        if (s.status === 'done' || s.status === 'failed') {
          session.current = null;
          letSleep();
          setPhase(s.status);
          if (s.status === 'done') setPasses((n) => n + 1);
          else setError(s.error || 'The hub stopped the recording without saying why.');
          onFinished();
        }
      } catch (err) {
        if (!alive) return;
        session.current = null;
        letSleep();
        setPhase('failed');
        setError(`The recording could not be followed: ${failureText(err)}`);
      }
    };
    const timer = window.setInterval(() => void poll(), 1000);
    void poll();
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [phase, letSleep, onFinished]);

  const start = async () => {
    setError(null);
    setStatus(null);
    setPhase('starting');
    try {
      const s = await startMapSession(room.area_id, tracker, duration);
      session.current = s.session_id;
      const h = await holdScreenOn();
      hold.current = h;
      setAwake(h);
      setStatus({ status: 'recording', samples: 0, scanners_heard: 0, seconds_left: duration });
      setPhase('recording');
    } catch (err) {
      setPhase('ready');
      setError(startErrorText(err, thisPhone));
    }
  };

  const cancel = async () => {
    const id = session.current;
    session.current = null;
    letSleep();
    setPhase('ready');
    setStatus(null);
    if (id) {
      try {
        await cancelMapSession(id);
      } catch (err) {
        setError(`The recording could not be stopped: ${failureText(err)}`);
      }
    }
  };

  return (
    <div className="space-y-4 p-4" data-testid="room-map-recorder">
      <button type="button" className={button} onClick={onBack} disabled={phase === 'recording' || phase === 'starting'}>
        <ArrowLeft size={14} /> Rooms
      </button>
      <h3 className="text-lg font-semibold text-white" data-testid="room-map-room">{room.name}</h3>

      {(phase === 'ready' || phase === 'starting') && (
        <>
          <p className="text-sm text-slate-300">
            Walk slowly around the room, including corners, holding the phone as you normally carry it.
            Keep walking until the countdown ends.
          </p>
          <label className="block text-xs text-slate-400">
            How long to record
            <select className={field} value={duration} onChange={(e) => setDuration(Number(e.target.value))} aria-label="How long to record">
              {[...PASS_SECONDS].sort((a, b) => a - b).map((s) => <option key={s} value={s}>{s < 60 ? `${s} seconds` : `${s / 60} minute${s > 60 ? 's' : ''}`}</option>)}
            </select>
          </label>
          <button type="button" className={`${primary} w-full sm:w-auto`} onClick={() => void start()} disabled={phase === 'starting'}
            data-testid="room-map-start">
            {phase === 'starting' ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />} Start
          </button>
        </>
      )}

      {phase === 'recording' && status && (
        <div className="space-y-3" role="status" aria-live="polite">
          <p className="text-sm text-slate-300">Keep walking around {room.name}.</p>
          <p className="text-5xl font-bold tabular-nums text-white" data-testid="room-map-countdown" aria-label="Time left">
            {clock(status.seconds_left)}
          </p>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-lg bg-white/5 p-3">
              <dt className="text-xs text-slate-400">Samples</dt>
              <dd className="text-xl font-semibold text-white" data-testid="room-map-samples">{status.samples}</dd>
            </div>
            <div className="rounded-lg bg-white/5 p-3">
              <dt className="text-xs text-slate-400">Listeners hearing it</dt>
              <dd className="text-xl font-semibold text-white" data-testid="room-map-scanners">{status.scanners_heard}</dd>
            </div>
          </dl>
          {awake === null && (
            <p className="text-xs text-amber-300" data-testid="room-map-wake-note">
              Keep the screen on while recording: this device would not hold it on by itself.
            </p>
          )}
          <button type="button" className={`${button} w-full sm:w-auto`} onClick={() => void cancel()}>
            <Square size={14} /> Cancel
          </button>
        </div>
      )}

      {phase === 'done' && status && (
        <div className="space-y-3" data-testid="room-map-done">
          <p className="flex items-center gap-2 text-sm font-semibold text-emerald-300">
            <CheckCircle2 size={16} /> Done
          </p>
          <p className="text-sm text-slate-300">
            {passes === 1 ? 'This pass' : `Pass ${passes}`} recorded {status.samples} samples from {status.scanners_heard}{' '}
            listener{status.scanners_heard === 1 ? '' : 's'}.
          </p>
          <p className="text-xs text-slate-400">More passes give better accuracy, especially at different times of day.</p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <button type="button" className={primary} onClick={() => { setPhase('ready'); setStatus(null); }}>
              <RotateCcw size={14} /> Record another pass
            </button>
            <button type="button" className={button} onClick={onBack}>Back to rooms</button>
          </div>
        </div>
      )}

      {phase === 'failed' && (
        <button type="button" className={button} onClick={() => { setPhase('ready'); setStatus(null); }}>
          <RotateCcw size={14} /> Try again
        </button>
      )}

      {error && <p className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-200" role="alert" data-testid="room-map-error">{error}</p>}
    </div>
  );
}

/**
 * Devices > Room detection (admins): how well the hub's Bluetooth room map
 * knows each room, and the "Map rooms" walk-through that records it. Built
 * for a phone, because the admin walks the house with it.
 */
export default function RoomMapPanel() {
  const queryClient = useQueryClient();
  const map = useQuery({ queryKey: MAP_KEY, queryFn: getRoomMap, retry: false });
  const areas = useQuery({ queryKey: ['room-map-areas'], queryFn: getEntries, retry: false, select: (d) => d.areas });
  const phones = useQuery({ queryKey: ['homehub-phones'], queryFn: getPhones, retry: false });
  const [beacon, setBeacon] = useState<BeaconStatus | null>(null);
  const [choice, setChoice] = useState<string>('');
  const [tag, setTag] = useState('');
  const [recording, setRecording] = useState<RoomRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (Capacitor.getPlatform() !== 'android') return undefined;
    let alive = true;
    BleBeacon.getStatus().then((s) => alive && setBeacon(s)).catch(() => alive && setBeacon(null));
    return () => { alive = false; };
  }, []);

  const live = beaconIsLive(beacon);
  const phoneOptions: TrackerOption[] = useMemo(() => phoneTrackers(phones.data?.phones ?? []), [phones.data]);
  const selected = choice || (live ? THIS_PHONE : phoneOptions.find((o) => !o.unavailable)?.id ?? TAG);
  const tracker = selected === THIS_PHONE && live && beacon ? beaconTrackerId(beacon) : selected === TAG ? tag.trim() : selected;
  const trackerProblem = selected === TAG && !validTagId(tag) ? 'Enter the tag id as uuid-major-minor.' : null;

  const rows = useMemo(() => roomRows(areas.data ?? [], map.data), [areas.data, map.data]);
  const names = useMemo(() => Object.fromEntries(rows.map((r) => [r.area_id, r.name])), [rows]);
  const pairs = useMemo(() => confusedPairs(map.data?.confusion, names), [map.data, names]);
  const refresh = useCallback(() => void queryClient.invalidateQueries({ queryKey: MAP_KEY }), [queryClient]);

  const clearRoom = useMutation({
    mutationFn: (room: RoomRow) => clearMappedRoom(room.area_id),
    onSuccess: (_r, room) => { toast.success(`${room.name} was cleared.`); refresh(); },
    onError: (err) => setError(failureText(err)),
  });
  const clearAll = useMutation({
    mutationFn: clearWholeMap,
    onSuccess: () => { toast.success('The room map was cleared.'); refresh(); },
    onError: (err) => setError(failureText(err)),
  });

  const loadError = map.error || areas.error;

  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="room-map">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-sky-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-sky-400">
          <Bluetooth size={14} /> Room detection
        </span>
        {map.data && <span className="text-xs text-slate-400" data-testid="room-map-overall">Accuracy {percent(map.data.overall_accuracy)}</span>}
      </header>

      {recording ? (
        <Recorder
          room={recording}
          tracker={tracker}
          thisPhone={selected === THIS_PHONE}
          onBack={() => setRecording(null)}
          onFinished={refresh}
        />
      ) : (
        <div className="space-y-4 p-4">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><MapPinned size={16} className="text-sky-400" /> Map rooms</h3>
            <p className="mt-1 text-xs text-slate-400">
              Carry a phone or tag around each room while the hub listens. The hub learns what each room sounds like to its
              Bluetooth listeners, and places people in rooms from that.
            </p>
          </div>

          <fieldset className="space-y-2" data-testid="room-map-tracker">
            <label className="block text-xs text-slate-400">
              What you will carry
              <select className={field} value={selected} onChange={(e) => setChoice(e.target.value)} aria-label="What you will carry">
                {live && <option value={THIS_PHONE}>This phone</option>}
                {phoneOptions.map((o) => (
                  <option key={o.id} value={o.id} disabled={!!o.unavailable}>
                    {o.label}{o.unavailable ? ` (${o.unavailable})` : ''}
                  </option>
                ))}
                <option value={TAG}>A tag or beacon id</option>
              </select>
            </label>
            {selected === TAG && (
              <label className="block text-xs text-slate-400">
                Tag id
                <input className={field} value={tag} onChange={(e) => setTag(e.target.value)} placeholder="uuid-major-minor"
                  aria-label="Tag id" autoCapitalize="off" autoCorrect="off" spellCheck={false} />
              </label>
            )}
            {beacon && !live && (
              <p className="text-xs text-amber-300" data-testid="room-map-beacon-off">
                This phone's beacon is not on the air{beacon.reason ? `: ${beacon.reason}` : ''}. Turn on Room detection in Settings to map with it.
              </p>
            )}
            {trackerProblem && tag && <p className="text-xs text-amber-300">{trackerProblem}</p>}
          </fieldset>

          {loadError && <p className="text-sm text-amber-300/90" role="alert" data-testid="room-map-load-error">{failureText(loadError)}</p>}
          {(map.isLoading || areas.isLoading) && <p className="text-sm text-slate-500">Loading rooms…</p>}
          {areas.data && areas.data.length === 0 && (
            <p className="text-sm text-slate-500">The hub has no rooms yet. Add rooms (areas) first, then map them.</p>
          )}

          <ul className="divide-y divide-white/5 rounded-xl border border-white/5" aria-label="Rooms">
            {rows.map((r) => (
              <li key={r.area_id} className="flex flex-wrap items-center gap-2 px-3 py-2" data-testid="room-row">
                <div className="min-w-0 flex-1">
                  <p className="break-words text-sm font-medium text-white">{r.name}</p>
                  <p className="text-xs text-slate-400">
                    <span className={`mr-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${STATE_TONE[r.state]}`}>{STATE_TEXT[r.state]}</span>
                    {r.state !== 'not_mapped' && `${percent(r.accuracy)} · ${r.sessions} pass${r.sessions === 1 ? '' : 'es'}`}
                    {r.last_recorded && ` · ${relativeTime(r.last_recorded)}`}
                  </p>
                  {r.staleWhy && r.staleWhy !== map.data?.stale_reason && <p className="text-xs text-amber-300">{r.staleWhy}</p>}
                </div>
                <div className="flex gap-2">
                  <button type="button" className={primary} disabled={!tracker || !!trackerProblem}
                    onClick={() => { setError(null); setRecording(r); }} aria-label={`Map ${r.name}`}>
                    {r.state === 'not_mapped' ? 'Map' : 'Add pass'}
                  </button>
                  {r.state !== 'not_mapped' && (
                    <button type="button" className={button} aria-label={`Clear ${r.name}`} disabled={clearRoom.isPending}
                      onClick={async () => {
                        if (await confirmDialog({ title: `Clear ${r.name}?`, body: 'Its recordings are deleted; map it again to detect it.', confirmLabel: 'Clear', danger: true })) clearRoom.mutate(r);
                      }}>
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>

          {map.data && (
            <div className="space-y-2 rounded-xl bg-white/5 p-3 text-sm" data-testid="room-map-summary">
              <p className="text-white">
                Overall accuracy <strong>{percent(map.data.overall_accuracy)}</strong>
                <span className="text-xs text-slate-400"> · {map.data.method === 'fingerprint' ? 'room fingerprints' : 'nearest listener'}</span>
              </p>
              {map.data.stale_reason && <p className="text-xs text-amber-300" data-testid="room-map-stale">{map.data.stale_reason}</p>}
              {pairs.length > 0 && (
                <div data-testid="room-map-confused">
                  <p className="text-xs text-slate-400">Rooms most often mixed up:</p>
                  <ul className="mt-1 space-y-1">
                    {pairs.map((p) => (
                      <li key={`${p.a}-${p.b}`} className="text-xs text-slate-200">
                        {p.a} and {p.b} ({p.count} sample{p.count === 1 ? '' : 's'})
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-xs text-slate-400">Record another pass in those rooms, or add another listener between them.</p>
                </div>
              )}
              {rows.some((r) => r.state !== 'not_mapped') && (
                <button type="button" className={button} disabled={clearAll.isPending}
                  onClick={async () => {
                    if (await confirmDialog({ title: 'Clear the whole room map?', body: 'Every room\'s recordings are deleted. Room detection stops until rooms are mapped again.', confirmLabel: 'Clear all', danger: true })) clearAll.mutate();
                  }}>
                  <Trash2 size={14} /> Clear all
                </button>
              )}
            </div>
          )}

          {error && <p className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-200" role="alert">{error}</p>}
        </div>
      )}
    </section>
  );
}

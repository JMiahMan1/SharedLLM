import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ChevronDown, ChevronLeft, ChevronRight, ChevronUp, House, Menu, Minus, Music, Pause, Play, Plus, SkipBack, SkipForward,
  Square, Undo2, Volume2, VolumeX,
} from 'lucide-react';
import BigSlider from './BigSlider';
import { LABEL, NotAvailable, PowerRow, ROUND, SELECT, SKY, type ControlProps } from './primitives';
import { getRooms, type HubEntity } from '../../../lib/dashboards';
import {
  MP, clock, keyCommand, mediaFeatures, mediaPosition, remoteOf, tvApps,
} from '../../../lib/deviceControls';
import { words } from '../../../lib/deviceTiles';

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v : '');

/** Cover art only from an address the browser can open itself (HA's proxy paths need HA's login). */
function artUrl(e: HubEntity): string | null {
  const pic = str(e.attributes?.entity_picture);
  return /^https:\/\//i.test(pic) ? pic : null;
}

/** Now playing: art, title, artist, and a position bar that can seek when the player can. */
function NowPlaying({ e, name, send, disabled }: Pick<ControlProps, 'e' | 'name' | 'send' | 'disabled'>) {
  const f = mediaFeatures(e);
  const a = e.attributes ?? {};
  const title = str(a.media_title) || str(a.app_name) || words(e.state);
  const sub = [str(a.media_artist) || str(a.media_series_title), str(a.media_album_name)].filter(Boolean).join(' · ');
  const duration = num(a.media_duration);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (e.state !== 'playing' || duration == null) return undefined;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [e.state, duration]);
  const pos = mediaPosition(e, now);
  const art = artUrl(e);
  const playing = e.state === 'playing';
  return (
    <div className="space-y-3" data-testid="now-playing">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-white/[0.06]">
          {art ? <img src={art} alt="" className="h-full w-full object-cover" /> : <Music size={24} className="text-slate-500" aria-hidden />}
        </div>
        <div className="min-w-0">
          <p className="truncate text-base font-semibold text-white">{title}</p>
          {sub && <p className="truncate text-sm text-slate-400">{sub}</p>}
        </div>
      </div>
      {duration != null && pos != null && (f.seek ? (
        <BigSlider label={clock(pos)} ariaLabel={`${name} position`} value={pos} min={0} max={duration} step={1}
          format={() => clock(duration)} fill="rgba(56,189,248,0.55)" disabled={disabled}
          onCommit={(v) => void send('media_seek', { seek_position: Math.round(v) })} />
      ) : (
        <div className="space-y-1" aria-label={`${clock(pos)} of ${clock(duration)}`}>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
            <div className="h-full rounded-full bg-sky-400" style={{ width: `${Math.min(100, (pos / duration) * 100)}%` }} />
          </div>
          <div className="flex justify-between text-[11px] tabular-nums text-slate-500"><span>{clock(pos)}</span><span>{clock(duration)}</span></div>
        </div>
      ))}
      <div className="flex items-center justify-center gap-4">
        {f.previous && <button type="button" className={ROUND} aria-label={`Previous on ${name}`} disabled={disabled}
          onClick={() => void send('media_previous_track')}><SkipBack size={20} /></button>}
        {f.play && <button type="button" aria-label={playing ? `Pause ${name}` : `Play ${name}`} disabled={disabled}
          onClick={() => void send(playing ? (f.pause ? 'media_pause' : 'media_stop') : 'media_play')}
          className="flex h-16 w-16 items-center justify-center rounded-full bg-sky-400 text-slate-950 shadow-lg outline-none hover:bg-sky-300 focus-visible:ring-2 focus-visible:ring-white disabled:opacity-40">
          {playing ? <Pause size={26} /> : <Play size={26} className="translate-x-0.5" />}
        </button>}
        {f.stop && !f.pause && !f.play && <button type="button" className={ROUND} aria-label={`Stop ${name}`} disabled={disabled}
          onClick={() => void send('media_stop')}><Square size={18} /></button>}
        {f.next && <button type="button" className={ROUND} aria-label={`Next on ${name}`} disabled={disabled}
          onClick={() => void send('media_next_track')}><SkipForward size={20} /></button>}
      </div>
    </div>
  );
}

function VolumeRow({ e, name, send, disabled }: Pick<ControlProps, 'e' | 'name' | 'send' | 'disabled'>) {
  const f = mediaFeatures(e);
  const vol = num(e.attributes?.volume_level);
  const muted = e.attributes?.is_volume_muted === true;
  if (!f.volume && !f.mute) return null;
  return (
    <div className="flex items-end gap-2">
      {f.mute && (
        <button type="button" className={`${ROUND.replace("h-14 w-14", "h-12 w-12")} rounded-2xl`} aria-label={muted ? `Unmute ${name}` : `Mute ${name}`}
          aria-pressed={muted} disabled={disabled} onClick={() => void send('volume_mute', { is_volume_muted: !muted })}>
          {muted ? <VolumeX size={20} /> : <Volume2 size={20} />}
        </button>
      )}
      {f.volume && vol != null && (
        <BigSlider label="Volume" ariaLabel={`${name} volume`} value={Math.round(vol * 100)} min={0} max={100}
          format={(v) => `${Math.round(v)}%`} fill={SKY} disabled={disabled}
          onCommit={(v) => void send('volume_set', { volume_level: Math.round(v) / 100 })} />
      )}
    </div>
  );
}

/** A remote key: a round button that sends one command. */
function Key({ label, onPress, disabled, children, className = '' }: {
  label: string; onPress: () => void; disabled?: boolean; children: React.ReactNode; className?: string;
}) {
  return (
    <button type="button" aria-label={label} disabled={disabled} onClick={onPress}
      className={`flex items-center justify-center text-slate-100 outline-none active:bg-white/20 focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-30 ${className}`}>
      {children}
    </button>
  );
}

/**
 * A TV: power, the remote (d-pad and OK, back, home, menu, volume and channel rockers) sent
 * through the device's remote entity, its apps or inputs, and now playing. Volume and mute go to
 * the TV itself when it takes them, else through the remote.
 */
export function TvControl({ e, name, send, call, disabled, siblings }: ControlProps) {
  const f = mediaFeatures(e);
  const remote = remoteOf(siblings);
  const off = e.state === 'off' || e.state === 'standby';
  const remoteOff = !remote || remote.state === 'unavailable';
  const press = (key: string) => {
    const cmd = keyCommand(remote, key);
    if (remote && cmd) void call('remote', 'send_command', remote.entity_id, { command: cmd });
  };
  const can = (key: string) => !remoteOff && keyCommand(remote, key) != null;
  const apps = tvApps(e, remote);
  const volUp = () => (f.volumeStep ? void send('volume_up') : press('volume_up'));
  const volDown = () => (f.volumeStep ? void send('volume_down') : press('volume_down'));
  const muted = e.attributes?.is_volume_muted === true;
  const togglePower = () => {
    if (f.power) void send(off ? 'turn_on' : 'turn_off');
    else if (remote) void call('remote', off ? 'turn_on' : 'turn_off', remote.entity_id);
  };
  const D = 'h-14 w-14 rounded-full hover:bg-white/10';
  return (
    <div className="space-y-4" data-testid="tv-control">
      {(f.power || remote) && <PowerRow on={!off} name={name} disabled={disabled} accent={SKY} onToggle={togglePower} />}
      {!off && (e.state === 'playing' || e.state === 'paused') && <NowPlaying e={e} name={name} send={send} disabled={disabled} />}

      <div className="rounded-3xl border border-white/10 bg-white/[0.03] p-4" data-testid="tv-remote" role="group" aria-label={`${name} remote`}>
        {remoteOff && (
          <div className="mb-3"><NotAvailable testId="remote-missing">
            {remote ? 'The remote is not answering, so the arrow keys are off.' : 'This TV has no remote connected, so arrow, back and home keys are not available.'}
          </NotAvailable></div>
        )}
        <div className="mx-auto grid w-[11.5rem] grid-cols-3 grid-rows-3 place-items-center rounded-full border border-white/10 bg-black/20 p-1">
          <span />
          <Key label="Up" disabled={disabled || !can('up')} onPress={() => press('up')} className={D}><ChevronUp size={24} /></Key>
          <span />
          <Key label="Left" disabled={disabled || !can('left')} onPress={() => press('left')} className={D}><ChevronLeft size={24} /></Key>
          <Key label="OK" disabled={disabled || !can('ok')} onPress={() => press('ok')}
            className="h-14 w-14 rounded-full bg-white/10 text-sm font-bold hover:bg-white/15">OK</Key>
          <Key label="Right" disabled={disabled || !can('right')} onPress={() => press('right')} className={D}><ChevronRight size={24} /></Key>
          <span />
          <Key label="Down" disabled={disabled || !can('down')} onPress={() => press('down')} className={D}><ChevronDown size={24} /></Key>
          <span />
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2">
          <Key label="Back" disabled={disabled || !can('back')} onPress={() => press('back')} className="min-h-12 gap-1.5 rounded-2xl bg-white/[0.06] text-sm">
            <Undo2 size={18} aria-hidden /> Back</Key>
          <Key label="Home" disabled={disabled || !can('home')} onPress={() => press('home')} className="min-h-12 gap-1.5 rounded-2xl bg-white/[0.06] text-sm">
            <House size={18} aria-hidden /> Home</Key>
          <Key label="Menu" disabled={disabled || !can('menu')} onPress={() => press('menu')} className="min-h-12 gap-1.5 rounded-2xl bg-white/[0.06] text-sm">
            <Menu size={18} aria-hidden /> Menu</Key>
        </div>
        <div className="mt-3 grid grid-cols-3 items-stretch gap-2">
          <div className="flex flex-col overflow-hidden rounded-2xl bg-white/[0.06]">
            <Key label="Volume up" disabled={disabled || (!f.volumeStep && !can('volume_up'))} onPress={volUp} className="min-h-12 hover:bg-white/10"><Plus size={18} /></Key>
            <span className="text-center text-[11px] text-slate-400">Vol</span>
            <Key label="Volume down" disabled={disabled || (!f.volumeStep && !can('volume_down'))} onPress={volDown} className="min-h-12 hover:bg-white/10"><Minus size={18} /></Key>
          </div>
          <Key label={muted ? 'Unmute' : 'Mute'} disabled={disabled || (!f.mute && !can('mute'))}
            onPress={() => (f.mute ? void send('volume_mute', { is_volume_muted: !muted }) : press('mute'))}
            className={`rounded-2xl text-sm ${muted ? 'bg-sky-500/20 text-sky-100' : 'bg-white/[0.06]'}`}>
            {muted ? <VolumeX size={22} /> : <Volume2 size={22} />}
          </Key>
          <div className="flex flex-col overflow-hidden rounded-2xl bg-white/[0.06]">
            <Key label="Channel up" disabled={disabled || !can('channel_up')} onPress={() => press('channel_up')} className="min-h-12 hover:bg-white/10"><ChevronUp size={18} /></Key>
            <span className="text-center text-[11px] text-slate-400">Ch</span>
            <Key label="Channel down" disabled={disabled || !can('channel_down')} onPress={() => press('channel_down')} className="min-h-12 hover:bg-white/10"><ChevronDown size={18} /></Key>
          </div>
        </div>
      </div>

      {apps.options.length > 0 && (
        <div data-testid="tv-apps">
          <p className={LABEL}>{apps.via === 'source' ? 'Inputs and apps' : 'Apps'}</p>
          <div role="group" aria-label={`${name} apps`} className="mt-1 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {apps.options.map((o) => (
              <button key={o} type="button" aria-pressed={o === apps.current} disabled={disabled}
                onClick={() => (apps.via === 'source' ? void send('select_source', { source: o })
                  : remote && void call('remote', 'turn_on', remote.entity_id, { activity: o }))}
                className={`min-h-11 truncate rounded-xl border px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40 ${
                  o === apps.current ? 'border-sky-400/60 bg-sky-500/15 font-semibold text-sky-100' : 'border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/10'}`}>
                {o}
              </button>
            ))}
          </div>
        </div>
      )}
      {f.volume && <VolumeRow e={e} name={name} send={send} disabled={disabled || off} />}
    </div>
  );
}

/** Speakers that can join this one (same grouping platform), from the Rooms feed. */
function useGroupable(e: HubEntity): HubEntity[] {
  const rooms = useQuery({ queryKey: ['dashboard', 'rooms'], queryFn: getRooms, staleTime: 15_000, retry: false });
  const all = [...(rooms.data?.areas.flatMap((a) => a.entities) ?? []), ...(rooms.data?.unassigned ?? [])];
  return all.filter((x) => x.domain === 'media_player' && x.entity_id !== e.entity_id
    && ((Number(x.attributes?.supported_features) || 0) & MP.GROUPING) !== 0);
}

function Grouping({ e, name, send, call, disabled }: Pick<ControlProps, 'e' | 'name' | 'send' | 'call' | 'disabled'>) {
  const others = useGroupable(e);
  const members = Array.isArray(e.attributes?.group_members) ? (e.attributes.group_members as string[]) : [];
  if (!others.length) return null;
  return (
    <div data-testid="speaker-group">
      <p className={LABEL}>Play on other speakers too</p>
      <div role="group" aria-label={`Speakers grouped with ${name}`} className="mt-1 flex flex-wrap gap-2">
        {others.map((o) => {
          const joined = members.includes(o.entity_id);
          return (
            <button key={o.entity_id} type="button" aria-pressed={joined} disabled={disabled}
              onClick={() => void (joined ? call('media_player', 'unjoin', o.entity_id)
                : send('join', { group_members: [o.entity_id] }))}
              className={`min-h-11 max-w-full truncate rounded-xl border px-3 text-sm disabled:opacity-40 ${
                joined ? 'border-sky-400/60 bg-sky-500/15 text-sky-100' : 'border-white/10 bg-white/[0.04] text-slate-200'}`}>
              {o.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** A speaker (or any player that is not a TV): now playing, volume, its sources and sound modes, grouping. */
export function SpeakerControl({ e, name, send, call, disabled }: ControlProps) {
  const f = mediaFeatures(e);
  const off = e.state === 'off' || e.state === 'standby';
  const a = e.attributes ?? {};
  const source = str(a.source);
  const mode = str(a.sound_mode);
  return (
    <div className="space-y-4" data-testid="media-control">
      {f.power && <PowerRow on={!off} name={name} disabled={disabled} accent={SKY} onToggle={() => void send(off ? 'turn_on' : 'turn_off')} />}
      {!off && (
        <>
          {e.state === 'idle' && !str(a.media_title)
            ? <p className="text-center text-sm text-slate-400">Nothing playing</p>
            : <NowPlaying e={e} name={name} send={send} disabled={disabled} />}
          <VolumeRow e={e} name={name} send={send} disabled={disabled} />
          {f.sources.length > 0 && (
            <label className={`block ${LABEL}`}>
              Source
              <select className={SELECT} value={f.sources.includes(source) ? source : ''} disabled={disabled}
                onChange={(ev) => void send('select_source', { source: ev.target.value })}>
                {!f.sources.includes(source) && <option value="" disabled>Choose a source</option>}
                {f.sources.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
          )}
          {f.soundModes.length > 0 && (
            <label className={`block ${LABEL}`}>
              Sound
              <select className={SELECT} value={f.soundModes.includes(mode) ? mode : ''} disabled={disabled}
                onChange={(ev) => void send('select_sound_mode', { sound_mode: ev.target.value })}>
                {!f.soundModes.includes(mode) && <option value="" disabled>Choose</option>}
                {f.soundModes.map((s) => <option key={s} value={s}>{words(s)}</option>)}
              </select>
            </label>
          )}
          {f.grouping && <Grouping e={e} name={name} send={send} call={call} disabled={disabled} />}
        </>
      )}
    </div>
  );
}

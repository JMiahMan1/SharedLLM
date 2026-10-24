import { useState, type ReactNode } from 'react';
import { Cast, Check, ChevronDown, Headphones, Speaker, Tv } from 'lucide-react';
import Modal from '../../../components/ui/Modal';
import { useMediaStore } from '../store/mediaStore';
import { useMediaScreen } from '../pages/MediaScreen';
import { muted, row, text } from './ui';

/**
 * "Playing on …": a chip in the header naming the output the controls drive
 * (UX-01), opening a list of this device and every speaker and TV, each with
 * what it is doing. Picking one only changes what the controls drive; moving
 * the music is the Now Playing sheet's "Play here".
 */
export function OutputPicker() {
  const { devices, selectDevice, player } = useMediaScreen();
  const target = useMediaStore((s) => s.activeOutputId);
  const players = useMediaStore((s) => s.players);
  const [open, setOpen] = useState(false);
  const current = target ? devices.find((d) => d.id === target)?.name ?? players[target]?.name ?? 'a speaker' : 'This device';
  const online = devices.filter((d) => d.online).length;

  // "Playing: Song" for a busy device (UX-37).
  const nowDoing = (id: string): string | null => {
    const p = players[id];
    if (!p?.item?.title || (p.status !== 'playing' && p.status !== 'paused')) return null;
    return `${p.status === 'playing' ? 'Playing' : 'Paused'}: ${p.item.title}`;
  };

  const pick = (id: string) => {
    selectDevice(id);
    setOpen(false);
  };

  const option = (id: string, name: string, detail: string, online: boolean, icon: ReactNode) => {
    const selected = id === target;
    return (
      <li key={id || 'this-device'}>
        <button
          type="button"
          onClick={() => pick(id)}
          aria-pressed={selected}
          className={`${row} ${selected ? 'bg-[var(--site-accent-soft)]' : ''} ${online ? '' : 'opacity-60'}`}
        >
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--site-card-bg-hover)] text-[var(--site-accent-text)]">
            {icon}
          </span>
          <span className="min-w-0 flex-1">
            <span className={`block truncate text-sm font-medium ${text}`}>{name}</span>
            <span className={`block truncate text-xs ${muted}`}>{online ? detail : 'Offline'}</span>
          </span>
          {selected ? <Check size={18} className="shrink-0 text-[var(--site-accent-text)]" aria-label="Selected" /> : null}
        </button>
      </li>
    );
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Playing on ${current}. Choose where to play`}
        className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-[var(--color-border-mid)] bg-[var(--site-card-bg)] px-4 text-sm text-[var(--site-text)] hover:bg-[var(--site-card-bg-hover)]"
      >
        <Cast size={16} className="shrink-0 text-[var(--site-accent-text)]" aria-hidden />
        <span className="truncate"><span className={muted}>Playing on </span>{current}</span>
        <ChevronDown size={16} className="shrink-0" aria-hidden />
      </button>
      <Modal isOpen={open} onClose={() => setOpen(false)} title="Playing on" size="md">
        <p className={`mb-2 text-xs ${muted}`}>{online} speaker{online === 1 ? '' : 's'} online</p>
        <ul className="space-y-1" aria-label="Outputs">
          {option('', 'This device', player.isPlaying ? 'Playing now' : 'This phone or browser', true, <Headphones size={18} aria-hidden />)}
          {devices.map((d) => option(
            d.id,
            d.name,
            nowDoing(d.id) ?? d.detail,
            d.online,
            d.detail === 'TV' ? <Tv size={18} aria-hidden /> : <Speaker size={18} aria-hidden />,
          ))}
          {devices.length === 0 ? (
            <li className={`px-3 py-4 text-sm ${muted}`}>No speakers found. Home Assistant players appear here once they are set up.</li>
          ) : null}
        </ul>
      </Modal>
    </>
  );
}

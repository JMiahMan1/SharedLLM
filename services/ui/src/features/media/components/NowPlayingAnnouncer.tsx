import { useState } from 'react';
import { useOptionalWebPlayer } from '../../../context/webPlayerContext';
import { useMediaStore } from '../store/mediaStore';

/**
 * The polite live region that tells a screen reader what started playing
 * (P7-T3): "Now playing <title> on <output>", and "Playing/Paused <title> on
 * <output>" when the state changes. It reads only the active output's title,
 * track identity and status, never the ticking position, so a running clock
 * does not spam it; a new track or a real state change announces once. The
 * announcement is adjusted during render (the same pattern the now-playing
 * sheet uses), so it never fires from an effect.
 */
export function NowPlayingAnnouncer() {
  const player = useOptionalWebPlayer();
  const activeId = useMediaStore((s) => s.activeOutputId);
  const remote = useMediaStore((s) => (activeId ? s.players[activeId] ?? null : null));

  const isWeb = !activeId;
  const title = (isWeb ? player?.mediaTitle : remote?.item?.title) ?? null;
  const uri = (isWeb ? player?.mediaUri : remote?.item?.uri) ?? null;
  const status = isWeb
    ? (player?.mediaTitle ? (player.isPlaying ? 'playing' : 'paused') : 'idle')
    : remote?.status ?? 'idle';
  const outputName = isWeb ? 'This device' : remote?.name ?? 'the selected speaker';

  const [announcement, setAnnouncement] = useState('');
  const [last, setLast] = useState({ key: '', status: '' });
  // A new track or a different output is a new announcement; a status change
  // is a one-line update. Position is deliberately absent from this key.
  const key = title ? `${activeId}\u0000${uri || title}` : '';

  if (!title) {
    if (last.key !== '' || announcement !== '') {
      setLast({ key: '', status: '' });
      setAnnouncement('');
    }
  } else if (last.key !== key) {
    setLast({ key, status });
    setAnnouncement(`Now playing ${title} on ${outputName}`);
  } else if (last.status !== status) {
    setLast({ key, status });
    if (status === 'playing' || status === 'paused') {
      setAnnouncement(`${status === 'playing' ? 'Playing' : 'Paused'} ${title} on ${outputName}`);
    }
  }

  return (
    <p
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-testid="now-playing-announcer"
      className="sr-only"
    >
      {announcement}
    </p>
  );
}

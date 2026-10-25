import MiniPlayer from './MiniPlayer';
import NowPlayingSheet from './NowPlayingSheet';
import QueueSheet from './QueueSheet';
import MediaShortcuts from './MediaShortcuts';
import { useMediaStore } from '../store/mediaStore';
import { useOptionalWebPlayer } from '../../../context/webPlayerContext';
import { useAbsProgressReporter } from '../engine/absProgress';

/**
 * The media surfaces every page shares, mounted once inside the router: the
 * mini player, the Now Playing and queue sheets, the keyboard shortcuts, and
 * Audiobookshelf progress saving.
 * Which sheet is open lives in the media store, so any screen (or a
 * shortcut) can open one.
 */
export default function MediaOverlays() {
  const sheets = useMediaStore((s) => s.sheets);
  const { openSheet, closeSheet } = useMediaStore((s) => s.actions);
  // A book's position is saved wherever it plays, on every page.
  useAbsProgressReporter(useOptionalWebPlayer());
  return (
    <>
      <MiniPlayer onOpen={() => openSheet('nowPlaying')} />
      <NowPlayingSheet
        open={sheets.nowPlaying}
        onClose={() => closeSheet('nowPlaying')}
        onOpenQueue={() => openSheet('queue')}
      />
      <QueueSheet open={sheets.queue} onClose={() => closeSheet('queue')} />
      <MediaShortcuts
        onOpenNowPlaying={() => openSheet('nowPlaying')}
        onOpenQueue={() => openSheet('queue')}
      />
    </>
  );
}

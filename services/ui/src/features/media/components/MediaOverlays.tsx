import MiniPlayer from '../../../components/media/MiniPlayer';
import NowPlayingSheet from '../../../components/media/NowPlayingSheet';
import QueueSheet from '../../../components/media/QueueSheet';
import MediaShortcuts from '../../../components/media/MediaShortcuts';
import { useMediaStore } from '../store/mediaStore';

/**
 * The media surfaces every page shares, mounted once inside the router: the
 * mini player, the Now Playing and queue sheets, and the keyboard shortcuts.
 * Which sheet is open lives in the media store, so any screen (or a
 * shortcut) can open one.
 */
export default function MediaOverlays() {
  const sheets = useMediaStore((s) => s.sheets);
  const { openSheet, closeSheet } = useMediaStore((s) => s.actions);
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

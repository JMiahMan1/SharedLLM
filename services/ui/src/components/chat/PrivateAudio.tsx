import { Loader2, Mic } from 'lucide-react';
import type { AudioPayload, PrivateVault } from '../../lib/privateChatCrypto';
import { usePrivateAudio } from './usePrivateAudio';
import { noSave } from './usePrivatePicture';
import { audioLength } from './privateAudioTime';

/**
 * A voice note sent in the chat: decrypted in memory and played in place, with
 * no download, share or save offered (`controlsList="nodownload"`, and the
 * element is in the no-save wrapper). A take that cannot be opened says so
 * instead of showing a dead player.
 */
export function PrivateAudioBubble({ token, payload, vault }: { token: string; payload: AudioPayload; vault: PrivateVault }) {
  const { url, failed } = usePrivateAudio(token, payload.blob, payload.mime, vault);
  if (failed) return <span className="italic opacity-80">This voice note could not be opened.</span>;
  if (!url) {
    return (
      <span className="flex h-12 w-56 max-w-full items-center justify-center rounded-2xl bg-black/20">
        <Loader2 size={16} className="animate-spin" />
      </span>
    );
  }
  return (
    <span className="block" {...noSave} data-testid="private-audio">
      <span className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold opacity-85">
        <Mic size={12} aria-hidden /> Voice message{payload.duration ? ` · ${audioLength(payload.duration)}` : ''}
      </span>
      <audio
        controls
        controlsList="nodownload noplaybackrate"
        preload="metadata"
        src={url}
        aria-label={payload.caption ? `Voice message: ${payload.caption}` : 'Voice message'}
        className="h-9 w-60 max-w-full"
        data-testid="private-audio-player"
        {...noSave}
      />
      {payload.caption && <span className="mt-1 block">{payload.caption}</span>}
    </span>
  );
}

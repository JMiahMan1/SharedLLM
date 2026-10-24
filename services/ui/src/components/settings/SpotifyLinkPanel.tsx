import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';

const RETURN_TEXT: Record<string, string> = {
  linked: 'Spotify is linked.',
  cancelled: 'Linking was cancelled.',
  failed: 'Spotify could not be linked. Try again.',
};

/**
 * Link your own Spotify (Phase 11, music everywhere): what you play in the
 * Spotify app lands on your activity feed, so Jarvis's music suggestions see
 * it too. Opt-in and yours to unlink. Needs the household's Spotify app to be
 * set up by an admin first; until then this says so and offers nothing.
 */
export default function SpotifyLinkPanel() {
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const returned = params.get('spotify');
  const { data: status, isLoading, isError } = useQuery({
    queryKey: ['spotify-status'],
    queryFn: () => api.getSpotifyStatus(),
    retry: false,
  });
  const link = useMutation({
    mutationFn: () => api.startSpotifyLink(),
    onSuccess: ({ url }) => {
      window.location.assign(url);
    },
  });
  const unlink = useMutation({
    mutationFn: () => api.unlinkSpotify(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['spotify-status'] }),
  });
  const failure = [link.error, unlink.error].find(Boolean);
  const failureText = isAxiosError(failure) ? (failure.response?.data as { detail?: string })?.detail || 'That did not work.' : failure ? 'That did not work.' : null;

  return (
    <div className="glass-panel rounded-2xl p-4 sm:p-5 space-y-2" data-testid="spotify-link">
      {returned && RETURN_TEXT[returned] && <p className="text-xs text-sky-300">{RETURN_TEXT[returned]}</p>}
      {isLoading ? (
        <div className="h-10 skeleton rounded-xl" aria-hidden />
      ) : isError || !status ? (
        <p className="text-xs text-amber-300">Spotify linking could not be checked.</p>
      ) : !status.configured ? (
        <p className="text-xs text-slate-400" data-testid="spotify-not-configured">
          Spotify linking is not set up on this server yet. An admin adds the household's Spotify app in Settings
          ({status.missing.join(', ')}).
        </p>
      ) : status.linked ? (
        <>
          <p className="text-sm text-slate-200" data-testid="spotify-linked">
            Linked{status.account_name ? ` as ${status.account_name}` : ''}. What you play in Spotify shows on your activity.
          </p>
          {status.last_error && <p className="text-xs text-amber-300">Last sync failed: {status.last_error}</p>}
          {status.last_sync_at && <p className="text-[11px] text-slate-500">Last synced {new Date(status.last_sync_at).toLocaleString()}</p>}
          <button type="button" disabled={unlink.isPending} onClick={() => unlink.mutate()} className="px-3 py-1.5 text-xs min-h-11 text-slate-400">
            Unlink Spotify
          </button>
        </>
      ) : (
        <>
          <p className="text-sm text-slate-300">Link your Spotify so what you play there counts in your listening and suggestions.</p>
          <button type="button" disabled={link.isPending} onClick={() => link.mutate()} className="glass-button px-3 py-1.5 text-xs min-h-11">
            Link Spotify
          </button>
        </>
      )}
      {failureText && <p className="text-xs text-amber-300">{failureText}</p>}
    </div>
  );
}

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { FileUp, Loader2, Music, Trash2 } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  acceptList, clipProblem, deleteHoldMusicClip, getHoldMusic, linesFromText, linesProblem, setHoldMusic,
  uploadHoldMusic, type HoldMusicSettings, type HoldMusicSource,
} from '../../lib/voiceHoldMusic';
import SetupSection from './SetupSection';
import { BTN, DANGER } from './setupStyles';

const KEY = ['homehub-voice-hold-music'];

const DESCRIPTIONS: Record<HoldMusicSource, string> = {
  gameshow: 'A playful, original thinking loop: pizzicato strings and a ticking clock.',
  builtin: 'Soft chords the hub makes itself.',
  upload: 'A clip of your own.',
};

function LinesEditor({ data, onSaved }: { data: HoldMusicSettings; onSaved: (d: HoldMusicSettings) => void }) {
  const [text, setText] = useState<string | null>(null);
  const value = text ?? data.lines.join('\n');
  const lines = linesFromText(value);
  const problem = linesProblem(lines, data.limits);
  const save = useMutation({
    mutationFn: (next: string[]) => setHoldMusic({ lines: next }),
    onSuccess: (d, next) => {
      onSaved(d);
      setText(null);
      toast.success(next.length ? 'Hold lines saved.' : 'The default hold lines are back.');
    },
    onError: (err) => toast.error(failureText(err)),
  });
  const changed = text !== null && lines.join('\n') !== data.lines.join('\n');
  return (
    <div className="space-y-2 border-t border-white/5 px-4 py-3">
      <label className="block text-xs text-slate-400" htmlFor="hold-lines">
        What Jarvis says before the music, one line each; one is picked at random.
      </label>
      <textarea id="hold-lines" rows={4} value={value} aria-label="Hold lines"
        placeholder={'Leave empty for the default lines, such as:\nLet me think about that... cue the thinking music.'}
        className="min-h-24 w-full min-w-0 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white placeholder:text-slate-500"
        onChange={(e) => setText(e.target.value)} />
      {problem && <p className="text-xs text-amber-300/90" role="alert">{problem}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} disabled={!changed || Boolean(problem) || save.isPending}
          onClick={() => save.mutate(lines)}>Save lines</button>
        {data.lines.length > 0 && (
          <button type="button" className={BTN} disabled={save.isPending} onClick={() => save.mutate([])}>
            Use the default lines
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Setup > Voice hold music: when Jarvis takes a while to answer, an Echo says a playful line and plays this
 * until the answer is ready. A game-show thinking loop (the default), the built-in tune, or a clip an admin
 * uploads. The change applies to the next wait; nothing restarts.
 */
export default function VoiceHoldMusicPanel() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const query = useQuery({ queryKey: KEY, queryFn: getHoldMusic, retry: false });
  const put = (d: HoldMusicSettings) => queryClient.setQueryData(KEY, d);
  const choose = useMutation({
    mutationFn: (source: HoldMusicSource) => setHoldMusic({ source }),
    onSuccess: put,
    onError: (err) => toast.error(failureText(err)),
  });
  const upload = useMutation({
    mutationFn: uploadHoldMusic,
    onSuccess: (d) => { put(d); toast.success('Your clip is now the hold music.'); },
    onError: (err) => setFileError(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: deleteHoldMusicClip,
    onSuccess: (d) => { put(d); toast.success('The clip was removed.'); },
    onError: (err) => toast.error(failureText(err)),
  });
  const data = query.data;
  const busy = choose.isPending || upload.isPending || remove.isPending;
  const pick = (file: File | undefined) => {
    if (!file || !data) return;
    const problem = clipProblem(file, data.limits);
    setFileError(problem);
    if (!problem) upload.mutate(file);
  };
  return (
    <SetupSection title="Voice hold music" icon={Music} testId="voice-hold-music">
      <p className="px-4 pt-3 text-xs text-slate-400">
        When Jarvis takes a while to answer, an Echo says a short line and plays this until the answer is ready.
      </p>
      {query.isLoading && <p className="p-4 text-sm text-slate-500">Loading…</p>}
      {query.error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(query.error)}</p>}
      {data && (
        <>
          <fieldset className="space-y-1 px-4 py-3" disabled={busy}>
            <legend className="sr-only">Hold music</legend>
            {data.sources.map((s) => {
              const needsClip = s.id === 'upload' && !data.upload;
              return (
                <label key={s.id} data-testid={`hold-music-${s.id}`}
                  className={`flex min-h-11 items-center gap-3 rounded-lg px-2 ${data.source === s.id ? 'bg-white/5' : ''} ${needsClip ? 'opacity-60' : 'cursor-pointer'}`}>
                  <input type="radio" name="hold-music" className="h-11 w-5 shrink-0" value={s.id}
                    checked={data.source === s.id} disabled={needsClip}
                    onChange={() => choose.mutate(s.id)} />
                  <span className="min-w-0 py-1.5">
                    <span className="block text-sm font-medium text-white">{s.label}</span>
                    <span className="block break-words text-xs text-slate-400">
                      {DESCRIPTIONS[s.id]}{needsClip ? ' Upload one below first.' : ''}
                    </span>
                  </span>
                </label>
              );
            })}
          </fieldset>
          <div className="space-y-2 border-t border-white/5 px-4 py-3" data-testid="hold-music-clip">
            {data.upload ? (
              <p className="break-words text-xs text-slate-300">
                Your clip: <span className="font-medium text-white">{data.upload.name}</span>
                {` · ${data.upload.seconds.toFixed(1)} s`}
                {data.upload.missing && <span className="text-amber-300/90"> · its file is missing, so the built-in tune plays</span>}
              </p>
            ) : (
              <p className="text-xs text-slate-400">
                {`A ${data.limits.types.join(', ')} file, up to ${Math.floor(data.limits.max_bytes / 1048576)} MB and ${data.limits.max_seconds} seconds. It loops, at the same volume as the other tunes.`}
              </p>
            )}
            <input ref={fileRef} type="file" accept={acceptList(data.limits)} className="hidden"
              data-testid="hold-music-file" aria-label="Hold music clip"
              onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ''; }} />
            <div className="flex flex-wrap gap-2">
              <button type="button" className={BTN} disabled={busy} onClick={() => fileRef.current?.click()}>
                {upload.isPending ? <Loader2 size={14} className="animate-spin" /> : <FileUp size={14} />}
                {data.upload ? 'Replace the clip' : 'Upload a clip'}
              </button>
              {data.upload && (
                <button type="button" className={DANGER} disabled={busy} onClick={() => remove.mutate()}
                  aria-label="Remove the uploaded clip">
                  <Trash2 size={14} /> Remove
                </button>
              )}
            </div>
            {fileError && <p className="break-words text-xs text-amber-300/90" role="alert">{fileError}</p>}
          </div>
          <LinesEditor data={data} onSaved={put} />
        </>
      )}
    </SetupSection>
  );
}

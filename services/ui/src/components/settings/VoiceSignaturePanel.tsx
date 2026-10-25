import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, Mic, Square, Trash2, UserCheck } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { VoiceCheck, VoiceStatus } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { useAudioRecorder } from '../../hooks/useAudioRecorder';

/** Sentences to read aloud, one per recording. Varied sounds make a steadier
 *  profile than the same words repeated. */
const VOICE_PHRASES = [
  'The quick brown fox jumps over the lazy dog by the river.',
  'Jarvis, please remind me to pick up groceries on the way home.',
  'Our family reads the Bible together after dinner most evenings.',
  "I'd like the living room lights dimmed and some quiet music playing.",
  'Sunday mornings start with pancakes, coffee and a walk outside.',
  'Call me when you get there, and drive safely in the rain.',
];
const ENROLL_SECONDS = 8;
const CHECK_SECONDS = 6;

type Purpose = 'enroll' | 'check';

/**
 * One timed take for a purpose, as a promise: the shared recorder stops by
 * itself after `limit` seconds and the panel shows the countdown.
 */
function useRecorder() {
  const [purpose, setPurpose] = useState<Purpose | null>(null);
  const pending = useRef<((blob: Blob | null) => void) | null>(null);
  const settle = (blob: Blob | null) => {
    setPurpose(null);
    pending.current?.(blob);
    pending.current = null;
  };
  const recorder = useAudioRecorder({
    onRecorded: (blob) => settle(blob),
    onError: (reason) => {
      toast.error(reason === 'denied' ? 'Microphone access was denied' : 'Recording is not available here');
      settle(null);
    },
  });

  const record = (forPurpose: Purpose, limit: number): Promise<Blob | null> =>
    new Promise((resolve) => {
      pending.current = resolve;
      setPurpose(forPurpose);
      void recorder.start(limit);
    });

  return { record, stop: recorder.stop, seconds: recorder.secondsLeft, purpose };
}

function statusLine(s: VoiceStatus): string {
  if (s.enrolled) return `Enrolled · ${s.samples} recording${s.samples === 1 ? '' : 's'}`;
  if (s.samples) return `${s.samples} of ${s.min_samples} recordings`;
  return 'Not set up';
}

function checkLine(c: VoiceCheck): string {
  if (c.is_me) return `Recognised you (${Math.round((c.score ?? 0) * 100)}% match).`;
  if (c.matched) return `That sounded like ${c.matched_name}, not you.`;
  const closest = c.candidates[0];
  return closest
    ? `Not sure who that was. Closest is ${closest.name} at ${Math.round(closest.score * 100)}%; try again closer to the microphone, or add a recording.`
    : 'Not sure who that was.';
}

/**
 * Enroll your voice so the household can tell who is speaking. Each recording
 * is turned into a voice signature on this server and the audio is discarded.
 */
export default function VoiceSignaturePanel() {
  const queryClient = useQueryClient();
  const { data: status, isError } = useQuery({ queryKey: ['my-voice'], queryFn: () => api.getMyVoice() });
  const { record, stop, seconds, purpose } = useRecorder();
  const [check, setCheck] = useState<VoiceCheck | null>(null);
  const [confirmForget, setConfirmForget] = useState(false);
  // Shown in the panel rather than a toast: it says what to change before the
  // next take, so it should still be there when the person reads it.
  const [problem, setProblem] = useState<string | null>(null);

  const saved = (s: VoiceStatus) => {
    queryClient.setQueryData(['my-voice'], s);
    queryClient.invalidateQueries({ queryKey: ['me'] });
  };
  const enroll = useMutation({
    mutationFn: (audio: Blob) => api.enrollVoice(audio),
    onSuccess: (s) => {
      saved(s);
      toast.success(s.enrolled && s.samples === s.min_samples ? 'Voice enrolled' : `Recording ${s.samples} saved`);
    },
    onError: (e) => setProblem(errorText(e, 'That recording could not be used')),
  });
  const test = useMutation({
    mutationFn: (audio: Blob) => api.checkMyVoice(audio),
    onSuccess: setCheck,
    onError: (e) => setProblem(errorText(e, 'Could not check that recording')),
  });
  const forget = useMutation({
    mutationFn: () => api.forgetMyVoice(),
    onSuccess: (s) => {
      saved(s);
      setCheck(null);
      setConfirmForget(false);
      toast.success('Voice profile erased');
    },
    onError: (e) => toast.error(errorText(e, 'Could not erase the voice profile')),
  });

  if (isError) {
    return <div className="glass-panel rounded-2xl p-5 text-sm text-red-300">Could not load your voice profile.</div>;
  }
  if (!status) {
    return <div className="glass-panel rounded-2xl p-5 text-sm text-slate-400">Loading your voice profile…</div>;
  }

  const busy = purpose !== null || enroll.isPending || test.isPending || forget.isPending;
  const phrase = VOICE_PHRASES[status.samples % VOICE_PHRASES.length];
  const canAdd = status.samples < status.max_samples;

  const takeRecording = async () => {
    setCheck(null);
    setProblem(null);
    const audio = await record('enroll', ENROLL_SECONDS);
    if (audio) enroll.mutate(audio);
  };
  const testRecording = async () => {
    setCheck(null);
    setProblem(null);
    const audio = await record('check', CHECK_SECONDS);
    if (audio) test.mutate(audio);
  };

  return (
    <div className="glass-panel rounded-2xl p-5 sm:p-6 flex flex-col gap-5" data-testid="voice-panel">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {status.enrolled ? <CheckCircle size={18} className="text-emerald-400 shrink-0" /> : <Mic size={18} className="text-slate-400 shrink-0" />}
          <span className="text-sm font-medium text-white" data-testid="voice-status">{statusLine(status)}</span>
        </div>
        <div className="flex gap-1.5" aria-hidden>
          {Array.from({ length: Math.max(status.min_samples, status.samples) }, (_, i) => (
            <span key={i} className={`h-2 w-2 rounded-full ${i < status.samples ? 'bg-emerald-400' : 'bg-white/15'}`} />
          ))}
        </div>
      </div>

      {status.enrolled && status.consistency !== null && (
        <p className="text-xs text-slate-400">
          Your recordings agree {Math.round(status.consistency * 100)}% with each other.
        </p>
      )}

      {(canAdd || purpose === 'enroll') && (
        <div className="rounded-xl bg-black/20 border border-white/10 p-4">
          <p className="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">
            {status.enrolled ? 'Optional: one more recording' : `Recording ${status.samples + 1} of ${status.min_samples}: read this aloud`}
          </p>
          <p className="mt-2 text-base sm:text-lg text-white leading-snug" data-testid="voice-phrase">“{phrase}”</p>
        </div>
      )}

      {purpose && (
        <p className="text-sm text-red-300 flex items-center gap-2" role="status">
          <span className="h-2 w-2 rounded-full bg-red-400 animate-pulse" />
          {purpose === 'enroll' ? 'Recording' : 'Listening'}… {seconds}s
        </p>
      )}
      {(enroll.isPending || test.isPending) && (
        <p className="text-sm text-slate-400" role="status">Working out your voice signature…</p>
      )}
      {problem && (
        <p className="text-sm text-red-300" role="alert" data-testid="voice-problem">
          {problem}
        </p>
      )}
      {check && (
        <p className={`text-sm ${check.is_me ? 'text-emerald-300' : 'text-amber-300'}`} data-testid="voice-check">
          {checkLine(check)}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {purpose ? (
          <button type="button" onClick={stop} className="glass-button min-h-11 px-4 flex items-center gap-2 text-sm bg-red-500/20 border-red-500/40 text-red-200">
            <Square size={16} /> Stop
          </button>
        ) : (
          <>
            {canAdd && (
              <button type="button" onClick={takeRecording} disabled={busy} className="glass-button min-h-11 px-4 flex items-center gap-2 text-sm bg-indigo-600/30 border-indigo-500/50 text-indigo-100 disabled:opacity-50">
                <Mic size={16} /> {status.samples ? 'Record next' : 'Start recording'}
              </button>
            )}
            {status.enrolled && (
              <button type="button" onClick={testRecording} disabled={busy} className="glass-button min-h-11 px-4 flex items-center gap-2 text-sm disabled:opacity-50">
                <UserCheck size={16} /> Test: who am I?
              </button>
            )}
            {status.samples > 0 &&
              (confirmForget ? (
                <span className="flex items-center gap-2">
                  <button type="button" onClick={() => forget.mutate()} disabled={busy} className="glass-button min-h-11 px-4 text-sm bg-red-500/20 border-red-500/40 text-red-200 disabled:opacity-50">
                    Erase voice profile
                  </button>
                  <button type="button" onClick={() => setConfirmForget(false)} className="min-h-11 px-3 text-sm text-slate-400">
                    Keep it
                  </button>
                </span>
              ) : (
                <button type="button" onClick={() => setConfirmForget(true)} disabled={busy} className="min-h-11 px-3 flex items-center gap-2 text-sm text-slate-400 hover:text-red-300 disabled:opacity-50">
                  <Trash2 size={16} /> {status.enrolled ? 'Forget my voice' : 'Start over'}
                </button>
              ))}
          </>
        )}
      </div>

      <p className="text-xs text-slate-500 leading-relaxed">
        Each recording is turned into a voice signature on this server and the audio is thrown away. Nothing is sent to an outside service.
        {status.enrolled
          ? ' You can also refresh it by voice: say “Hey Jarvis, learn my voice” at a satellite, then talk normally for three commands.'
          : ' Once enrolled, you can say “Hey Jarvis, learn my voice” at a satellite to add three more samples by talking normally.'}
      </p>
    </div>
  );
}

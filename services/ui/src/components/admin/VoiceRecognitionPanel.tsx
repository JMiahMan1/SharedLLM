import type { FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AudioLines, Check, RefreshCw, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { NewVoiceSeen, ScoreSpread, VoiceAdminOverview, VoiceCalibration } from '../../types/api';
import { errorText } from '../../lib/errorText';
import { newVoiceLine, satelliteName } from '../../lib/voiceAdmin';

/**
 * Admin > Users & Devices > Voice recognition.
 *
 * Who has enrolled (counts only, never a recording), the threshold calibrated on
 * the household's own enrolled voices (identity voice.calibrate), and commands
 * heard at someone's own satellite that sounded close to them: approving one adds
 * it to their voice as an auto sample (never displacing one they recorded).
 * Voices nobody enrolled that keep talking to a satellite are listed as counts
 * only ("a new voice talked to the Office Echo 5 times this week"): they are
 * anonymous, expire after a week and never become a profile.
 */

const STATUS_WORDS: Record<VoiceCalibration['status'], string> = {
  default: 'Nobody has enrolled yet: the built-in threshold is used.',
  single: 'Only one person has enrolled, so there is no one to tell them apart from yet. The threshold stays just under their own scores.',
  separated: 'Every enrolled voice is told apart: other people score below the threshold, and each person mostly above it.',
  overlap: 'Some voices overlap. Jarvis stays silent rather than guess, so those people are recognised less often until they add recordings, ideally at their own satellite.',
};

const pct = (n: number | null | undefined) => (n == null ? '-' : `${Math.round(n * 100)}%`);

function Spread({ label, s }: { label: string; s: ScoreSpread | null }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/5 p-3 text-xs">
      <p className="mb-1 font-medium uppercase tracking-wider text-slate-500">{label}</p>
      {s ? (
        <p className="text-slate-200">
          {s.n} scores · lowest {pct(s.min)} · typical {pct(s.median)} · highest {pct(s.max)}
        </p>
      ) : (
        <p className="text-slate-500">None yet</p>
      )}
    </div>
  );
}

export const VoiceRecognitionPanel: FC = () => {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery<VoiceAdminOverview>({ queryKey: ['voice-admin'], queryFn: () => api.getVoiceAdmin() });
  const done = () => queryClient.invalidateQueries({ queryKey: ['voice-admin'] });

  const recalibrate = useMutation({
    mutationFn: () => api.recalibrateVoice(),
    onSuccess: () => { void done(); toast.success('Calibrated'); },
    onError: (e) => toast.error(errorText(e, 'Could not calibrate')),
  });
  const approve = useMutation({
    mutationFn: (id: number) => api.approveVoiceCandidate(id),
    onSuccess: () => { void done(); toast.success('Added to their voice'); },
    onError: (e) => toast.error(errorText(e, 'Could not add it')),
  });
  const reject = useMutation({
    mutationFn: (id: number) => api.rejectVoiceCandidate(id),
    onSuccess: () => { void done(); toast.success('Rejected'); },
    onError: (e) => toast.error(errorText(e, 'Could not reject it')),
  });
  const forget = useMutation({
    mutationFn: (v: NewVoiceSeen) => api.forgetNewVoice(v.id, v.device),
    onSuccess: () => { void done(); toast.success('Dismissed'); },
    onError: (e) => toast.error(errorText(e, 'Could not dismiss it')),
  });
  const clearAuto = useMutation({
    mutationFn: (username: string) => api.clearAutoVoiceSamples(username),
    onSuccess: () => { void done(); toast.success('Auto-added samples removed'); },
    onError: (e) => toast.error(errorText(e, 'Could not remove them')),
  });
  const busy = approve.isPending || reject.isPending;
  const cal = data?.calibration;
  const missing = (data?.people ?? []).filter((p) => !p.enrolled);

  return (
    <section className="glass-panel min-w-0 overflow-hidden p-4 sm:p-6" data-testid="voice-recognition-panel">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <AudioLines size={20} className="text-violet-300" />
            Voice recognition
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Who Jarvis can recognise by voice, and how sure it has to be. When it is not sure it says nothing, and
            the request goes to the person the satellite is assigned to, else the default user.
          </p>
        </div>
        <button type="button" onClick={() => recalibrate.mutate()} disabled={recalibrate.isPending}
          className="glass-button flex min-h-11 shrink-0 items-center gap-2 px-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-50">
          <RefreshCw size={14} className={recalibrate.isPending ? 'animate-spin' : ''} /> Calibrate now
        </button>
      </div>

      {isError && <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">{errorText(error, 'Could not load voice recognition')}</p>}
      {isPending && <p className="text-sm text-slate-400">Loading…</p>}

      {cal && (
        <div className="mb-5 space-y-2" data-testid="voice-calibration">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-xl border border-white/5 bg-white/5 p-3"><p className="text-[11px] uppercase tracking-wider text-slate-500">Threshold</p><p className="text-lg font-bold text-white">{pct(cal.threshold)}</p></div>
            <div className="rounded-xl border border-white/5 bg-white/5 p-3"><p className="text-[11px] uppercase tracking-wider text-slate-500">Lead needed</p><p className="text-lg font-bold text-white">{pct(cal.margin)}</p></div>
            <div className="rounded-xl border border-white/5 bg-white/5 p-3"><p className="text-[11px] uppercase tracking-wider text-slate-500">Enrolled</p><p className="text-lg font-bold text-white">{cal.people}</p></div>
            <div className="rounded-xl border border-white/5 bg-white/5 p-3"><p className="text-[11px] uppercase tracking-wider text-slate-500">To review</p><p className="text-lg font-bold text-white">{data?.candidates.length ?? 0}</p></div>
          </div>
          <p className="text-sm text-slate-300">{STATUS_WORDS[cal.status]}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <Spread label="Same person" s={cal.same_speaker} />
            <Spread label="Other people" s={cal.other_speaker} />
          </div>
        </div>
      )}

      {data && (
        <div className="mb-5">
          <p className="px-1 pb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">People</p>
          <ul className="space-y-2">
            {data.people.map((p) => (
              <li key={p.username} className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-sm" data-testid="voice-person">
                <span className="min-w-0 truncate text-white">{p.name}</span>
                <span className={`shrink-0 text-xs ${p.enrolled ? 'text-emerald-200' : 'text-amber-200'}`}>
                  {p.enrolled
                    ? `${p.samples} recordings${p.satellite_samples ? `, ${p.satellite_samples} at a satellite` : ''}${p.auto_samples ? `, ${p.auto_samples} auto-added` : ''}`
                    : p.samples ? `${p.samples} of ${data.min_samples} recordings` : 'Not enrolled'}
                </span>
                {p.auto_samples > 0 && (
                  <button type="button" onClick={() => clearAuto.mutate(p.username)} disabled={clearAuto.isPending}
                    className="min-h-11 shrink-0 rounded-lg px-2 text-xs text-slate-400 underline disabled:opacity-50">
                    Remove auto-added
                  </button>
                )}
              </li>
            ))}
          </ul>
          {missing.length > 0 && (
            <p className="mt-2 text-xs text-slate-400">
              To enrol, each person opens Jarvis on their own phone, goes to Identity, Vocal Signature and records{' '}
              {data.min_samples} short sentences (about a minute), or says &ldquo;Jarvis, learn my voice&rdquo; at a
              satellite assigned to them. Recording at the satellite they use most helps it recognise them there.
            </p>
          )}
        </div>
      )}

      {data && (data.new_voices?.length ?? 0) > 0 && (
        <div className="mb-5">
          <p className="px-1 pb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">New voices</p>
          <ul className="space-y-2" data-testid="voice-new-voices">
            {(data.new_voices ?? []).map((v) => (
              <li key={`${v.device}:${v.id}`} className="flex min-w-0 flex-col gap-2 rounded-xl border border-sky-400/20 bg-sky-500/5 p-3 text-sm sm:flex-row sm:items-center">
                <p className="min-w-0 flex-1 break-words text-slate-200">{newVoiceLine(v)}</p>
                <button type="button" disabled={forget.isPending} onClick={() => forget.mutate(v)}
                  aria-label={`Dismiss the new voice at ${v.label || 'this satellite'}`}
                  className="glass-button flex min-h-11 shrink-0 items-center justify-center gap-1 px-3 text-xs disabled:opacity-50">
                  <X size={14} /> Dismiss
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-400">
            Counts only: Jarvis keeps no recording and no profile of these voices, and forgets them a week after
            it last heard them. If someone new lives here, add them in People; they can then say &ldquo;Jarvis,
            learn my voice&rdquo; at a satellite assigned to them.
          </p>
        </div>
      )}

      {data && data.candidates.length > 0 && (
        <div>
          <p className="px-1 pb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">Heard at their own satellite</p>
          <ul className="space-y-2" data-testid="voice-candidates">
            {data.candidates.map((c) => (
              <li key={c.id} className="flex min-w-0 flex-col gap-2 rounded-xl border border-violet-400/20 bg-violet-500/5 p-3 text-sm sm:flex-row sm:items-center">
                <p className="min-w-0 flex-1 text-slate-200">
                  Sounded like <span className="font-semibold text-white">{c.name}</span> ({pct(c.score)}
                  {c.runner_up != null ? `, next ${pct(c.runner_up)}` : ''}) at {satelliteName(c.satellite)},{' '}
                  {new Date(c.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                </p>
                <div className="flex shrink-0 gap-2">
                  <button type="button" disabled={busy} onClick={() => approve.mutate(c.id)} aria-label={`Approve sample for ${c.name}`}
                    className="glass-button flex min-h-11 items-center gap-1 px-3 text-xs disabled:opacity-50">
                    <Check size={14} /> It was them
                  </button>
                  <button type="button" disabled={busy} onClick={() => reject.mutate(c.id)} aria-label={`Reject sample for ${c.name}`}
                    className="glass-button flex min-h-11 items-center gap-1 px-3 text-xs disabled:opacity-50">
                    <X size={14} /> Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
};

export default VoiceRecognitionPanel;

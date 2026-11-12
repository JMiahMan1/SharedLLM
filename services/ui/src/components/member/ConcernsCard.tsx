import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertOctagon, AlertTriangle, Check, Download, Info, ShieldAlert, X } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { downloadBlobUrl } from '../../lib/artifactKinds';
import { errorText } from '../../lib/errorText';
import { relativeTime } from '../../lib/wanderTrips';
import MemberCard from './MemberCard';
import type { ConcernLevel, GuardianConcern } from '../../types/api';
import { useMemberDeviceNoun } from '../../hooks/useDeviceNoun';

const LEVEL: Record<ConcernLevel, { word: string; icon: typeof AlertTriangle; style: string }> = {
  severe: { word: 'Severe', icon: AlertOctagon, style: 'border-red-400/40 bg-red-400/10 text-red-200' },
  high: { word: 'High', icon: AlertTriangle, style: 'border-amber-400/40 bg-amber-400/10 text-amber-200' },
  medium: { word: 'Worth a look', icon: Info, style: 'border-white/10 bg-white/5 text-slate-300' },
};

const WHERE: Record<string, string> = {
  sms: 'a text message',
  web: 'a website',
  media: 'something played',
  media_scan: 'a photo or video',
  page: 'an app',
  calls: 'a call',
};

function whereText(feature?: string, action?: string): string {
  if (feature === 'media' && (action?.includes('photo') || action?.includes('image') || action?.includes('video') || action?.includes('scan'))) {
    return 'a photo or video';
  }
  return WHERE[feature || ''] || 'their activity';
}

function whenText(value?: string): string {
  if (!value) return '';
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? value : when.toLocaleString();
}

/**
 * Bark-style concern cards: what content monitoring found, how serious it is,
 * the words that raised it (in context), and a way to start talking about it
 * from the week's guidance. Concerns the local model dismissed in context
 * say so rather than disappearing.
 *
 * Admins and the child's parents can download the whole retained list, add a
 * human verdict (confirmed or false alarm), and export a flagged image for
 * review. Images are never displayed inside Jarvis: the export is the only
 * way to see one. The server checks all of this again; this only keeps the
 * controls off everyone else's screen.
 */
/** Save a downloaded blob, then release its object URL. */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  downloadBlobUrl(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** The server's reason for a failed download. A blob request's error body is a
 *  Blob, so it is read and parsed before errorText can see its `detail`. */
async function downloadError(err: unknown, fallback: string): Promise<string> {
  const data = (err as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      return errorText({ response: { data: parsed } }, fallback);
    } catch {
      return fallback;
    }
  }
  return errorText(err, fallback);
}

export default function ConcernsCard({ username, name, limit = 5 }: { username: string; name: string; limit?: number }) {
  const device = useMemberDeviceNoun(username);
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [exporting, setExporting] = useState<'csv' | 'json' | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [imageBusy, setImageBusy] = useState<string | null>(null);
  const canExport = Boolean(user?.is_admin || user?.family_role === 'father' || user?.family_role === 'mother');
  const concerns = useQuery({ queryKey: ['guardian-concerns', username], queryFn: () => api.getGuardianConcerns(username, 50), retry: false });
  const guidance = useQuery({ queryKey: ['guardian-guidance', username], queryFn: () => api.getGuardianGuidance(username), retry: false });
  const starters = new Map((guidance.data?.prompts || []).map((p) => [p.signal, p.starter]));
  const list = (concerns.data?.concerns || []).slice(0, limit);
  const total = concerns.data?.concerns.length ?? 0;

  async function exportConcerns(format: 'csv' | 'json') {
    setExporting(format);
    setExportError(null);
    try {
      const blob = await api.exportGuardianConcerns(username, format);
      const stamp = new Date().toISOString().slice(0, 10);
      saveBlob(blob, `jarvis-concerns-${username}-${stamp}.${format}`);
    } catch (err) {
      setExportError(await downloadError(err, 'The export could not be downloaded.'));
    } finally {
      setExporting(null);
    }
  }

  async function reviewConcern(id: string, verdict: 'confirmed' | 'dismissed') {
    setReviewing(`${id}:${verdict}`);
    setReviewError(null);
    try {
      await api.reviewGuardianConcern(username, id, verdict, notes[id] || '');
      setNotes((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: ['guardian-concerns', username] });
    } catch (err) {
      setReviewError(errorText(err, 'The review could not be saved.'));
    } finally {
      setReviewing(null);
    }
  }

  async function downloadImage(concern: GuardianConcern) {
    if (!concern.id) return;
    setImageBusy(concern.id);
    setReviewError(null);
    try {
      const { blob, filename } = await api.downloadGuardianConcernImage(username, concern.id);
      saveBlob(blob, filename);
    } catch (err) {
      setReviewError(await downloadError(err, 'The image could not be downloaded.'));
    } finally {
      setImageBusy(null);
    }
  }

  return (
    <MemberCard
      title="Concerns"
      icon={ShieldAlert}
      tone="text-red-300"
      testId="member-concerns"
      isLoading={concerns.isLoading}
      error={concerns.isError ? 'Concerns could not be loaded.' : null}
      empty={list.length === 0 ? `Nothing of concern found on ${name}'s ${device}.` : null}
      aside={
        <div className="flex items-center gap-2">
          {total > limit && <span>{limit} of {total}</span>}
          {canExport && (
            <span className="flex items-center gap-1" data-testid="concerns-export">
              {(['csv', 'json'] as const).map((format) => (
                <button
                  key={format}
                  type="button"
                  onClick={() => void exportConcerns(format)}
                  disabled={exporting !== null}
                  title={`Download all ${total} retained concerns as ${format.toUpperCase()}`}
                  className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-200 transition hover:border-sky-300/40 hover:text-white disabled:cursor-wait disabled:opacity-50"
                >
                  <Download size={10} />
                  {exporting === format ? '…' : format}
                </button>
              ))}
            </span>
          )}
        </div>
      }
    >
      {exportError && (
        <p className="mb-2 rounded-lg border border-amber-400/20 bg-amber-400/5 px-2.5 py-2 text-[11px] text-amber-300" data-testid="concerns-export-error">
          {exportError}
        </p>
      )}
      {reviewError && (
        <p className="mb-2 rounded-lg border border-amber-400/20 bg-amber-400/5 px-2.5 py-2 text-[11px] text-amber-300" data-testid="concerns-review-error">
          {reviewError}
        </p>
      )}
      <ul className="space-y-2.5">
        {list.map((c, i) => {
          const level = LEVEL[c.level] || LEVEL.medium;
          const Icon = level.icon;
          const dismissed = c.review?.verdict === 'dismissed';
          const when = whenText(c.at);
          const context = c.context && c.context !== c.excerpt ? c.context : '';
          const currentNote = c.id ? (notes[c.id] ?? c.human_review?.note ?? '') : '';
          return (
            <li key={c.id || `${c.category}-${c.at}-${i}`} className={`rounded-xl border p-3.5 text-xs space-y-2 ${level.style}`} data-testid="concern">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 font-semibold">
                  <Icon size={14} className="shrink-0" /> {c.label} · {level.word}
                </p>
                {c.at && <span className="rounded-full bg-black/20 px-2 py-0.5 font-normal text-[10px] opacity-80" title={when}>{relativeTime(c.at)}</span>}
              </div>
              <p className="rounded-lg bg-black/20 px-2.5 py-2 leading-relaxed opacity-95">
                In {whereText(c.feature, c.action)}{c.item ? ` from ${c.item}` : ''}: “{c.excerpt}”
              </p>
              {(context || when || c.file_name) && (
                <details className="rounded-lg border border-white/10 bg-black/10 px-2.5 py-2 text-[11px] leading-relaxed">
                  <summary className="cursor-pointer font-semibold opacity-85">More context</summary>
                  <div className="mt-1.5 space-y-1 opacity-85">
                    {when && <p><span className="opacity-70">When: </span>{when}</p>}
                    {c.item && <p><span className="opacity-70">Source: </span>{c.item}</p>}
                    {c.file_name && <p><span className="opacity-70">File: </span>{c.file_name}</p>}
                    {context && <p><span className="opacity-70">Around it: </span>{context}</p>}
                  </div>
                </details>
              )}
              {c.has_image && c.id && canExport && (
                <button
                  type="button"
                  onClick={() => void downloadImage(c)}
                  disabled={imageBusy !== null}
                  className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[10px] font-semibold text-slate-200 transition hover:border-sky-300/40 hover:text-white disabled:cursor-wait disabled:opacity-50"
                  data-testid="concern-image-export"
                  title="Images are never shown in Jarvis. Download the file to review it safely."
                >
                  <Download size={11} />
                  {imageBusy === c.id ? 'Preparing…' : 'Download image'}
                </button>
              )}
              {c.review?.reason && (
                <p className="text-[11px] leading-relaxed opacity-85">
                  {dismissed ? <span className="font-semibold text-emerald-300">Probably fine: </span> : ''}
                  {c.review.reason}
                </p>
              )}
              {c.human_review && (
                <p className="rounded-lg border border-emerald-300/20 bg-emerald-300/5 px-2.5 py-2 text-[11px] leading-relaxed text-emerald-100" data-testid="concern-human-review">
                  <span className="font-semibold">
                    {c.human_review.verdict === 'confirmed' ? 'Confirmed' : 'Marked as a false alarm'}
                  </span>
                  {' '}by {c.human_review.by}
                  {c.human_review.at ? ` · ${whenText(c.human_review.at)}` : ''}
                  {c.human_review.note ? `: “${c.human_review.note}”` : ''}
                </p>
              )}
              {canExport && c.id && (
                <div className="flex flex-wrap items-center gap-1.5" data-testid="concern-review">
                  <button
                    type="button"
                    onClick={() => void reviewConcern(c.id!, 'confirmed')}
                    disabled={reviewing !== null}
                    className="inline-flex items-center gap-1 rounded-md border border-emerald-300/30 bg-emerald-300/10 px-2 py-1 text-[10px] font-semibold text-emerald-100 transition hover:border-emerald-200/60 disabled:cursor-wait disabled:opacity-50"
                  >
                    <Check size={11} /> Confirm
                  </button>
                  <button
                    type="button"
                    onClick={() => void reviewConcern(c.id!, 'dismissed')}
                    disabled={reviewing !== null}
                    className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[10px] font-semibold text-slate-200 transition hover:border-sky-300/40 disabled:cursor-wait disabled:opacity-50"
                  >
                    <X size={11} /> False alarm
                  </button>
                  <input
                    value={currentNote}
                    onChange={(event) => setNotes((current) => ({ ...current, [c.id!]: event.target.value }))}
                    placeholder="Note (optional)"
                    maxLength={500}
                    className="min-w-[8rem] flex-1 rounded-md border border-white/10 bg-black/20 px-2 py-1 text-[10px] text-slate-100 placeholder:text-slate-400 focus:border-sky-300/40 focus:outline-none"
                  />
                </div>
              )}
              {starters.get(c.category) && (
                <div className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-2 text-slate-200">
                  <span className="font-semibold text-sky-200">What to say: </span>
                  {starters.get(c.category)}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </MemberCard>
  );
}

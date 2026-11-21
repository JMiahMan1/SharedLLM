import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, GraduationCap, Lightbulb } from 'lucide-react';
import toast from 'react-hot-toast';
import { errorText } from '../../lib/errorText';
import {
  healthRows,
  outcomeTone,
  passRateLabel,
  scrubLabel,
  scrubTone,
  teacherTrend,
  type TeacherExport,
} from '../../lib/trainer';
import { api } from '../../services/api';

const FILTERS: { key: string; label: string; params: { outcome?: string; status?: string } }[] = [
  { key: 'all', label: 'All', params: {} },
  { key: 'success', label: 'Verified', params: { outcome: 'success' } },
  { key: 'failure', label: 'Failed', params: { outcome: 'failure' } },
  { key: 'unverified', label: 'Unverified', params: { outcome: 'unverified' } },
  { key: 'dropped', label: 'Dropped', params: { status: 'dropped' } },
];

/**
 * Teacher traces: the work strong cloud models did on this repository, scrubbed
 * on the Mac that held the transcripts and imported here. The card shows what
 * was kept and why some was not, the lessons written from verified work, and
 * how the local model scores on the same tasks run by run.
 */
export default function TeacherTracesCard() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('all');
  const [format, setFormat] = useState<'openai' | 'sharegpt'>('openai');
  const [lastExport, setLastExport] = useState<TeacherExport | null>(null);

  const summaryQuery = useQuery({ queryKey: ['trainer', 'teacher', 'summary'], queryFn: () => api.teacherSummary() });
  const active = FILTERS.find((item) => item.key === filter) ?? FILTERS[0];
  const tracesQuery = useQuery({
    queryKey: ['trainer', 'teacher', 'traces', filter],
    queryFn: () => api.teacherTraces({ limit: 50, ...active.params }),
  });

  const lessons = useMutation({
    mutationFn: () => api.writeTeacherLessons({ limit: 20 }),
    onSuccess: (result) => {
      const failed = result.failed.length ? `, ${result.failed.length} refused by memory` : '';
      toast.success(`${result.written.length} worked-example lesson(s) written${failed}`);
      queryClient.invalidateQueries({ queryKey: ['trainer', 'teacher'] });
    },
    onError: (error) => toast.error(errorText(error, 'The lessons could not be written')),
  });

  const dataset = useMutation({
    mutationFn: () => api.exportTeacherDataset({ format, eval_share: 0.1 }),
    onSuccess: (result) => {
      setLastExport(result);
      toast.success(`Dataset written: ${result.train_rows} train, ${result.eval_rows} eval rows`);
      queryClient.invalidateQueries({ queryKey: ['trainer', 'teacher', 'summary'] });
    },
    onError: (error) => toast.error(errorText(error, 'The dataset could not be exported')),
  });

  const summary = summaryQuery.data;
  const trend = teacherTrend(summary?.scores);
  const kept = (summary?.traces.by_status.clean ?? 0) + (summary?.traces.by_status.masked ?? 0);
  const exported = lastExport ?? summary?.exports?.[0] ?? null;

  return (
    <section className="rounded-2xl border border-white/10 bg-white/5 p-5" data-testid="teacher-traces">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-lg font-black uppercase tracking-tight text-white">
            <GraduationCap size={16} /> Teacher traces
          </h3>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            Work that strong cloud models did on this code, scrubbed of secrets and names before it left the Mac.
            Verified work becomes lessons Raven retrieves, every task becomes a test for the local model, and the
            whole set exports as fine-tuning data.
          </p>
        </div>
      </div>

      {summaryQuery.isError ? (
        <p className="mt-4 text-sm text-amber-300" data-testid="teacher-error">
          {errorText(summaryQuery.error, 'The teacher traces could not be read')}
        </p>
      ) : null}

      {summary?.health ? (
        <div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-3" data-testid="teacher-health">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">
            Runs on its own · last pass {summary.health.last_pass_at ? new Date(summary.health.last_pass_at).toLocaleString() : 'not yet'}
          </p>
          <ul className="mt-2 space-y-1">
            {healthRows(summary.health).map((row) => (
              <li key={row.label} className="flex flex-wrap justify-between gap-x-3 text-[11px]">
                <span className="text-slate-400">{row.label}</span>
                <span className={`min-w-0 break-words ${row.failing ? 'text-rose-300' : 'text-slate-200'}`}>{row.value}</span>
              </li>
            ))}
          </ul>
          {summary.health.failures.length ? (
            <ul className="mt-2 space-y-1" data-testid="teacher-failures">
              {summary.health.failures.map((failure) => (
                <li key={failure.part} className="break-words rounded-lg border border-rose-500/30 bg-rose-500/10 p-2 text-[11px] text-rose-300">
                  {failure.label}: {failure.reason}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Traces" value={summary?.traces.total ?? '-'} />
        <Stat label="Kept" value={summary ? kept : '-'} />
        <Stat label="Dropped" value={summary?.traces.by_status.dropped ?? (summary ? 0 : '-')} />
        <Stat label="Lessons" value={summary?.lessons.written ?? '-'} />
        <Stat label="Test cases" value={summary?.eval_cases ?? '-'} />
        <Stat label="Local score" value={trend.latest} testId="teacher-score" />
      </div>
      <p className="mt-2 text-[11px] text-slate-400" data-testid="teacher-trend">
        Local model on teacher tasks: {trend.change}
        {trend.runs ? ` (${trend.runs} run${trend.runs === 1 ? '' : 's'})` : ''}
        {summary ? ` · ${summary.traces.masked_values} values masked, ${summary.traces.dropped_steps} steps removed` : ''}
      </p>

      {summary?.scores.length ? (
        <ul className="mt-3 space-y-1" data-testid="teacher-scores">
          {summary.scores.slice(-8).map((row) => (
            <li key={row.report_id} className="flex items-center gap-3">
              <span className="w-24 shrink-0 text-[10px] font-black uppercase tracking-wider text-slate-400">
                {row.started_at ? new Date(row.started_at).toLocaleDateString() : '-'}
              </span>
              <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-white/10">
                <div
                  className={
                    row.pass_rate >= 0.8
                      ? 'h-full bg-emerald-500'
                      : row.pass_rate >= 0.4
                        ? 'h-full bg-amber-500'
                        : 'h-full bg-rose-500'
                  }
                  style={{ width: `${Math.round(row.pass_rate * 100)}%` }}
                />
              </div>
              <span className="w-20 shrink-0 text-right text-[10px] font-black text-slate-400">
                {row.passed}/{row.cases} · {passRateLabel(row.pass_rate)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          disabled={lessons.isPending || !summary?.lessons.eligible}
          onClick={() => lessons.mutate()}
          className="flex min-h-11 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-40"
        >
          <Lightbulb size={14} /> Write lessons ({summary?.lessons.eligible ?? 0} ready)
        </button>
        <label className="flex min-h-11 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500">
          Format
          <select
            value={format}
            onChange={(event) => setFormat(event.target.value as 'openai' | 'sharegpt')}
            className="rounded-lg border border-white/10 bg-black/30 px-2 py-2 text-white"
          >
            <option value="openai">OpenAI messages</option>
            <option value="sharegpt">ShareGPT</option>
          </select>
        </label>
        <button
          disabled={dataset.isPending || !kept}
          onClick={() => dataset.mutate()}
          className="flex min-h-11 items-center gap-2 rounded-xl border border-white/10 px-4 text-[10px] font-black uppercase tracking-widest text-slate-300 hover:text-white disabled:opacity-40"
        >
          <Download size={14} /> Export dataset
        </button>
      </div>
      {exported ? (
        <p className="mt-2 break-all text-[11px] text-slate-400" data-testid="teacher-export">
          Last export ({exported.format}): {exported.train_rows} train / {exported.eval_rows} eval rows ·{' '}
          {exported.paths.train}
        </p>
      ) : null}

      <div className="mt-5 flex flex-wrap gap-2" role="tablist" aria-label="Filter teacher traces">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            role="tab"
            aria-selected={filter === item.key}
            onClick={() => setFilter(item.key)}
            className={`min-h-11 rounded-lg px-3 text-[10px] font-black uppercase tracking-wider ${
              filter === item.key ? 'bg-indigo-600/40 text-white' : 'border border-white/10 text-slate-500 hover:text-white'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tracesQuery.data && tracesQuery.data.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400" data-testid="teacher-empty">
          No traces here yet. On the Mac, run <code className="text-slate-300">scripts/collect_teacher_traces.py</code>{' '}
          (see docs/TEACHER_TRACES.md) or enable the opencode trainer-sync plugin.
        </p>
      ) : null}

      <ul className="mt-4 space-y-2" data-testid="teacher-trace-list">
        {(tracesQuery.data ?? []).map((row) => (
          <li key={row.id} className="rounded-xl border border-white/10 bg-black/20 p-3" data-testid="teacher-trace">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="min-w-0 flex-1 break-words text-xs font-bold text-slate-200">
                {row.goal || <span className="italic text-slate-500">content removed by the scrubber</span>}
              </p>
              <span
                className={`shrink-0 rounded-lg border px-2 py-1 text-[10px] font-black uppercase tracking-wider ${scrubTone(row.scrub_status)}`}
              >
                {scrubLabel(row)}
              </span>
            </div>
            <p className="mt-1 break-words text-[11px] text-slate-400">
              <span className={`font-black uppercase ${outcomeTone(row.outcome)}`}>{row.outcome}</span>
              {' · '}
              {row.model || 'unknown model'} · {row.source}
              {row.started_at ? ` · ${new Date(row.started_at).toLocaleDateString()}` : ''}
              {row.step_count ? ` · ${row.step_count} steps` : ''}
              {row.lesson_id ? ' · lesson written' : ''}
              {row.case_id ? ' · test case' : ''}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Stat({ label, value, testId }: { label: string; value: number | string; testId?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-white/10 bg-black/20 p-3" data-testid={testId}>
      <p className="truncate text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-black text-white">{value}</p>
    </div>
  );
}

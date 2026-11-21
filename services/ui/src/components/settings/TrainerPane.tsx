import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, FlaskConical, Image as ImageIcon, Play, RefreshCcw, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import { errorText } from '../../lib/errorText';
import {
  benchmarkLine,
  bucketBars,
  freeVramLabel,
  hardwareVerdict,
  passRateLabel,
  proposalTone,
  summariseCatalog,
} from '../../lib/trainer';
import { api } from '../../services/api';
import TeacherTracesCard from './TeacherTracesCard';

const BUCKET_CHOICES = ['repair', 'writing', 'vision', 'workspace', 'memory', 'protocol', 'tools', 'teacher'];

/**
 * Training: what Jarvis can do, how the local model does on it, and what the
 * last facilitator pass decided to improve. Every action here is admin-gated on
 * the server; this pane only ever shows what the caller is allowed to see.
 */
export default function TrainerPane() {
  const queryClient = useQueryClient();
  const [selectedBuckets, setSelectedBuckets] = useState<string[]>([]);
  const [limit, setLimit] = useState(12);

  const catalogQuery = useQuery({ queryKey: ['trainer', 'catalog'], queryFn: () => api.trainerCatalog() });
  const curriculumQuery = useQuery({
    queryKey: ['trainer', 'curriculum'],
    queryFn: () => api.trainerCurriculum(undefined, 500),
  });
  const backlogQuery = useQuery({ queryKey: ['trainer', 'backlog'], queryFn: () => api.trainerBacklog() });
  const hardwareQuery = useQuery({ queryKey: ['trainer', 'hardware'], queryFn: () => api.trainerHardware() });
  const modelsQuery = useQuery({ queryKey: ['trainer', 'models'], queryFn: () => api.trainerModels() });
  const benchmarksQuery = useQuery({
    queryKey: ['trainer', 'benchmarks'],
    queryFn: () => api.trainerBenchmarks(),
  });
  const reportsQuery = useQuery({ queryKey: ['trainer', 'reports'], queryFn: () => api.trainerReports() });
  const latestReportQuery = useQuery({
    queryKey: ['trainer', 'report', reportsQuery.data?.[0]?.id],
    queryFn: () => api.trainerReport(reportsQuery.data![0].id),
    enabled: Boolean(reportsQuery.data?.[0]?.id),
  });

  const catalogSummary = useMemo(
    () => (catalogQuery.data ? summariseCatalog(catalogQuery.data) : null),
    [catalogQuery.data],
  );

  const runEval = useMutation({
    mutationFn: () =>
      api.runTrainerEval({
        limit,
        budget_s: 600,
        ...(selectedBuckets.length ? { buckets: selectedBuckets } : {}),
      }),
    onSuccess: (report) => {
      toast.success(`Evaluated ${report.totals.cases} cases: ${passRateLabel(report.totals.pass_rate)} passed`);
      queryClient.invalidateQueries({ queryKey: ['trainer', 'reports'] });
    },
    onError: (error) => toast.error(errorText(error, 'The evaluation could not run')),
  });

  const runCycle = useMutation({
    mutationFn: () => api.runTrainerCycle({ limit, budget_s: 600, apply_lessons: true }),
    onSuccess: (cycle) => {
      toast.success(`${cycle.proposals.length} improvement proposals from this pass`);
      queryClient.invalidateQueries({ queryKey: ['trainer', 'reports'] });
      queryClient.invalidateQueries({ queryKey: ['trainer', 'backlog'] });
    },
    onError: (error) => toast.error(errorText(error, 'The cycle could not run')),
  });

  const busy = runEval.isPending || runCycle.isPending;
  const report = latestReportQuery.data;
  const backlog = backlogQuery.data;

  return (
    <div className="space-y-8">
      <HostCard
        hardware={hardwareQuery.data}
        models={modelsQuery.data}
        benchmarks={benchmarksQuery.data}
        modelsError={modelsQuery.isError ? errorText(modelsQuery.error, 'The model list could not be read') : ''}
        benchmarksError={
          benchmarksQuery.isError ? errorText(benchmarksQuery.error, 'The benchmark results could not be read') : ''
        }
      />
      <section className="rounded-2xl border border-white/10 bg-white/5 p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-black uppercase tracking-tight text-white">The local model's classroom</h3>
            <p className="mt-1 max-w-2xl text-sm text-slate-400">
              The curriculum is derived from Jarvis's own routes, tools and screens, plus the household's real
              work: fixing its own errors, drafting sermons, editing images, and keeping its memory tidy.
            </p>
          </div>
          <button
            onClick={() => queryClient.invalidateQueries({ queryKey: ['trainer'] })}
            className="flex min-h-11 items-center gap-2 rounded-xl border border-white/10 px-4 text-[10px] font-black uppercase tracking-widest text-slate-300 hover:text-white"
          >
            <RefreshCcw size={14} /> Refresh
          </button>
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Capabilities" value={catalogSummary?.total ?? '—'} />
          <Stat label="Tools" value={catalogSummary?.tools ?? '—'} />
          <Stat label="Screens" value={catalogSummary?.pages ?? '—'} />
          <Stat label="Cases" value={curriculumQuery.data?.summary.total ?? '—'} />
        </div>

        {curriculumQuery.data ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {Object.entries(curriculumQuery.data.summary.by_bucket).map(([bucket, count]) => (
              <span
                key={bucket}
                className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-slate-400"
              >
                {bucket} · {count}
              </span>
            ))}
          </div>
        ) : null}
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/5 p-5">
        <h3 className="text-lg font-black uppercase tracking-tight text-white">Run a pass</h3>
        <p className="mt-1 text-sm text-slate-400">
          A run is sequential and bounded: one short answer per case, then it stops. Failures become proposals;
          proposals become lessons the next mission carries.
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {BUCKET_CHOICES.map((bucket) => {
            const on = selectedBuckets.includes(bucket);
            return (
              <button
                key={bucket}
                onClick={() =>
                  setSelectedBuckets((current) =>
                    on ? current.filter((item) => item !== bucket) : [...current, bucket],
                  )
                }
                className={`min-h-9 rounded-lg px-3 text-[10px] font-black uppercase tracking-wider ${
                  on ? 'bg-indigo-600/40 text-white' : 'border border-white/10 text-slate-500 hover:text-white'
                }`}
              >
                {bucket}
              </button>
            );
          })}
          <label className="ml-auto flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500">
            Cases
            <input
              type="number"
              min={1}
              max={200}
              value={limit}
              onChange={(event) => setLimit(Math.max(1, Number(event.target.value) || 1))}
              className="w-20 rounded-lg border border-white/10 bg-black/30 px-2 py-1 text-white"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap gap-3">
          <button
            disabled={busy}
            onClick={() => runEval.mutate()}
            className="flex min-h-11 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-40"
          >
            <Play size={14} /> Evaluate
          </button>
          <button
            disabled={busy}
            onClick={() => runCycle.mutate()}
            className="flex min-h-11 items-center gap-2 rounded-xl border border-purple-500/30 bg-purple-500/10 px-4 text-[10px] font-black uppercase tracking-widest text-purple-300 disabled:opacity-40"
          >
            <FlaskConical size={14} /> Dream + evaluate + propose
          </button>
        </div>
      </section>

      {report ? (
        <section className="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-lg font-black uppercase tracking-tight text-white">Last report</h3>
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              {report.model || 'no model recorded'} · {passRateLabel(report.totals.pass_rate)} of{' '}
              {report.totals.cases} cases
            </span>
          </div>
          <div className="mt-4 space-y-2">
            {bucketBars(report.by_bucket).map((bar) => (
              <div key={bar.bucket} className="flex items-center gap-3">
                <span className="w-28 text-[10px] font-black uppercase tracking-wider text-slate-400">
                  {bar.bucket}
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/10">
                  <div
                    className={
                      bar.passRate >= 80
                        ? 'h-full bg-emerald-500'
                        : bar.passRate >= 40
                          ? 'h-full bg-amber-500'
                          : 'h-full bg-rose-500'
                    }
                    style={{ width: `${bar.passRate}%` }}
                  />
                </div>
                <span className="w-16 text-right text-[10px] font-black text-slate-400">
                  {bar.passed}/{bar.cases}
                </span>
              </div>
            ))}
          </div>
          <ul className="mt-4 space-y-2">
            {report.cases
              .filter((item) => !item.passed)
              .slice(0, 5)
              .map((item) => (
                <li key={item.id} className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-3">
                  <p className="text-xs font-bold text-rose-200">{item.title}</p>
                  <p className="mt-1 text-[11px] text-slate-400">
                    {(item.error || item.failed_checks.join('; ')).slice(0, 240)}
                  </p>
                </li>
              ))}
          </ul>
        </section>
      ) : null}

      <section className="rounded-2xl border border-white/10 bg-white/5 p-5">
        <h3 className="text-lg font-black uppercase tracking-tight text-white">Improvement backlog</h3>
        {backlog?.generated_at ? (
          <p className="mt-1 text-sm text-slate-400">
            From the pass on {new Date(backlog.generated_at).toLocaleString()}; {backlog.proposals.length}{' '}
            proposals, {backlog.lessons.length} written into memory.
          </p>
        ) : (
          <p className="mt-1 text-sm text-slate-400">No pass has run yet.</p>
        )}
        <ul className="mt-4 space-y-3">
          {(backlog?.proposals ?? []).map((proposal) => (
            <li key={proposal.id} className={`rounded-xl border p-3 ${proposalTone(proposal.kind)}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-black uppercase tracking-wider">{proposal.title}</span>
                <span className="text-[10px] font-black uppercase tracking-widest opacity-70">
                  {proposal.kind} · p{proposal.priority}
                </span>
              </div>
              <p className="mt-1 text-[11px] text-slate-300">{proposal.detail}</p>
              <p className="mt-1 text-[11px] italic text-slate-400">{proposal.action}</p>
            </li>
          ))}
        </ul>
      </section>

      <TeacherTracesCard />

      <WrittenWorks />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 p-3">
      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-black text-white">{value}</p>
    </div>
  );
}

/**
 * The host: Alpaca owns the GPU, so the trainer asks it what it has and whether a
 * pass may start. When the household is using the model, this card says so
 * instead of letting a run fail on timeouts.
 */
function HostCard({
  hardware,
  models,
  benchmarks,
  modelsError = '',
  benchmarksError = '',
}: {
  hardware?: Awaited<ReturnType<typeof api.trainerHardware>>;
  models?: Awaited<ReturnType<typeof api.trainerModels>>;
  benchmarks?: Awaited<ReturnType<typeof api.trainerBenchmarks>>;
  modelsError?: string;
  benchmarksError?: string;
}) {
  const verdict = hardwareVerdict(hardware);
  const loaded = (hardware?.loaded_models ?? []).map((item) => item.model).filter(Boolean) as string[];

  return (
    <section className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-black uppercase tracking-tight text-white">Model host</h3>
          <p className="mt-1 text-sm text-slate-400">
            Alpaca holds the GPU. The trainer asks it for a free slot before every call and steps aside when
            the household is using the model.
          </p>
        </div>
        <span
          className={`rounded-xl border px-3 py-2 text-[10px] font-black uppercase tracking-widest ${
            verdict.ready
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
          }`}
        >
          {verdict.ready ? 'ready' : 'busy'} · {verdict.label}
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="GPU" value={freeVramLabel(hardware)} />
        <Stat label="Loaded" value={loaded.length ? loaded.join(', ') : 'none'} />
        <Stat label="Queued" value={hardware?.queued_requests ?? '—'} />
        <Stat label="LoRA" value={hardware?.lora?.total ?? 0} />
      </div>

      <p className="mt-3 text-[11px] text-slate-400">
        Trainer model: <span className="font-bold text-slate-200">{models?.chosen || 'not configured'}</span>
        {models?.trainer_model_missing ? (
          <span className="text-amber-300">
            {' '}
            trainer_model names a model this host does not have, so training will not run until it is installed
            or the setting is changed.
          </span>
        ) : null}
        {hardware?.context_length ? ` · ${hardware.context_length} token context` : ''}
        {hardware?.base_url ? ` · ${hardware.base_url}` : ''}
      </p>
      <p className="mt-1 text-[11px] text-slate-400">
        Benchmark: <span className="text-slate-300">{benchmarkLine(benchmarks)}</span>
        {benchmarks?.chosen_model ? ` · ${benchmarks.chosen_model}` : ''}
      </p>
      {[...(hardware?.errors ?? []), modelsError, benchmarksError].filter(Boolean).length ? (
        <ul className="mt-2 list-disc pl-5 text-[11px] text-amber-300">
          {[...(hardware?.errors ?? []), modelsError, benchmarksError].filter(Boolean).map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}


/**
 * Written works: a sermon grounded on a passage, and an illustration edited in
 * the same workspace. The file lands as `sermons/<slug>.md`, so it opens in the
 * Workspaces page like any other document.
 */
function WrittenWorks() {
  const workspacesQuery = useQuery({ queryKey: ['workspaces'], queryFn: () => api.getWorkspaces() });
  const [workspaceId, setWorkspaceId] = useState('');
  const [passage, setPassage] = useState('Romans 8:28');
  const [title, setTitle] = useState('');
  const [points, setPoints] = useState(3);
  const [series, setSeries] = useState('');
  const [imagePath, setImagePath] = useState('');
  const [imagePrompt, setImagePrompt] = useState('');
  const [drafted, setDrafted] = useState<{ title: string; path: string; words: number; notes: string[] } | null>(
    null,
  );

  const chosen = workspaceId || workspacesQuery.data?.[0]?.id || '';

  const draft = useMutation({
    mutationFn: () =>
      api.draftSermon({
        workspace_id: chosen,
        passage,
        title: title || undefined,
        series: series || undefined,
        points,
      }),
    onSuccess: (sermon) => {
      setDrafted(sermon);
      toast.success(`Saved ${sermon.path}`);
    },
    onError: (error) => toast.error(errorText(error, 'The sermon could not be drafted')),
  });

  const illustrate = useMutation({
    mutationFn: () =>
      api.illustrateInWorkspace({ workspace_id: chosen, image_path: imagePath, prompt: imagePrompt }),
    onSuccess: (result) => {
      if (result.status === 'SUCCESS') toast.success(`Saved ${result.output_path ?? 'the edited image'}`);
      else toast.error(result.message || 'The image edit failed');
    },
    onError: (error) => toast.error(errorText(error, 'The image could not be edited')),
  });

  const field = 'mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-2 py-2 text-sm font-normal text-white';
  const label = 'text-[10px] font-black uppercase tracking-widest text-slate-500';

  return (
    <section className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h3 className="flex items-center gap-2 text-lg font-black uppercase tracking-tight text-white">
        <BookOpen size={16} /> Written works and pictures
      </h3>
      <p className="mt-1 text-sm text-slate-400">
        A sermon is drafted in the outlined parts a small model can hold: an outline, then one paragraph per
        point. Scripture is read from the Bible service first, so the text is quoted rather than remembered.
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className={label}>
          Workspace
          <select value={chosen} onChange={(event) => setWorkspaceId(event.target.value)} className={field}>
            {(workspacesQuery.data ?? []).map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.display_name || workspace.id}
              </option>
            ))}
          </select>
        </label>
        <label className={label}>
          Passage
          <input value={passage} onChange={(event) => setPassage(event.target.value)} className={field} />
        </label>
        <label className={label}>
          Title (optional)
          <input value={title} onChange={(event) => setTitle(event.target.value)} className={field} />
        </label>
        <label className={label}>
          Series (optional)
          <input value={series} onChange={(event) => setSeries(event.target.value)} className={field} />
        </label>
        <label className={label}>
          Points
          <input
            type="number"
            min={1}
            max={6}
            value={points}
            onChange={(event) => setPoints(Math.min(6, Math.max(1, Number(event.target.value) || 1)))}
            className={field}
          />
        </label>
        <div className="flex items-end">
          <button
            disabled={draft.isPending || !chosen || !passage.trim()}
            onClick={() => draft.mutate()}
            className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-40"
          >
            <Sparkles size={14} /> Draft sermon
          </button>
        </div>
      </div>

      {drafted ? (
        <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
          <p className="text-xs font-bold text-emerald-200">
            {drafted.title} · {drafted.words} words · {drafted.path}
          </p>
          {drafted.notes.length ? (
            <ul className="mt-1 list-disc pl-5 text-[11px] text-amber-300">
              {drafted.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="mt-6 grid gap-3 md:grid-cols-3">
        <label className={label}>
          Image path
          <input
            value={imagePath}
            onChange={(event) => setImagePath(event.target.value)}
            placeholder="art/base.png"
            className={field}
          />
        </label>
        <label className={`${label} md:col-span-2`}>
          Edit instruction
          <input
            value={imagePrompt}
            onChange={(event) => setImagePrompt(event.target.value)}
            placeholder="make it a watercolour painting, keeping the faces the same"
            className={field}
          />
        </label>
      </div>
      <button
        disabled={illustrate.isPending || !chosen || !imagePath.trim() || !imagePrompt.trim()}
        onClick={() => illustrate.mutate()}
        className="mt-3 flex min-h-11 items-center gap-2 rounded-xl border border-cyan-500/30 bg-cyan-500/10 px-4 text-[10px] font-black uppercase tracking-widest text-cyan-300 disabled:opacity-40"
      >
        <ImageIcon size={14} /> Edit image in workspace
      </button>
    </section>
  );
}

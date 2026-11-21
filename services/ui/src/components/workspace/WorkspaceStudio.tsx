import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Hammer, Image as ImageIcon, Play, Square, Sparkles, Stethoscope, Wand2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { errorText } from '../../lib/errorText';
import {
  previewState,
  runSummary,
  stackLine,
  stepsFor,
  stlEditLine,
  stlSizeLine,
  stlVerdict,
  verifyLine,
  type StlEdit,
  type StlSummary,
  type StudioVerdict,
} from '../../lib/studio';
import { api } from '../../services/api';

/**
 * Studio: the workspace as a place to build, test, preview and make pictures.
 *
 * The panel never decides what a step *is* -- the gateway detects the project and
 * returns the commands, so the same buttons work for a Vite app, a Python
 * package, a Go module or a folder of HTML. It only shows what the workspace can
 * do and what happened when it did it.
 */
export default function WorkspaceStudio({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [output, setOutput] = useState('');
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.studioPreview>> | null>(null);
  const [artPrompt, setArtPrompt] = useState('');
  const [artName, setArtName] = useState('');
  const [deckTitle, setDeckTitle] = useState('');
  const [deckBody, setDeckBody] = useState('# Title\n- a point\n\n---\n\n# Next\n- another');
  const [gameTitle, setGameTitle] = useState('');
  const [verdict, setVerdict] = useState<StudioVerdict | null>(null);
  const [modelPath, setModelPath] = useState('');
  const [fitMm, setFitMm] = useState('');
  const [model, setModel] = useState<StlSummary | null>(null);
  const [edit, setEdit] = useState<StlEdit | null>(null);
  const [busy, setBusy] = useState(false);

  const profileQuery = useQuery({
    queryKey: ['studio', 'profile', workspaceId],
    queryFn: () => api.studioProfile(workspaceId),
    enabled: Boolean(workspaceId),
  });
  const assetsQuery = useQuery({
    queryKey: ['studio', 'assets', workspaceId],
    queryFn: () => api.studioAssets(workspaceId),
    enabled: Boolean(workspaceId),
  });

  const steps = stepsFor(profileQuery.data);

  const run = useMutation({
    mutationFn: (action: string) => api.studioRun(workspaceId, action),
    onMutate: () => {
      setBusy(true);
      setOutput('');
    },
    onSuccess: (result) => {
      const detail = [runSummary(result), result.stdout, result.stderr].filter(Boolean).join('\n\n');
      setOutput(detail.trim());
      if (result.ok) toast.success(runSummary(result));
      else toast.error(runSummary(result));
    },
    onError: (error) => setOutput(errorText(error, 'The step could not run')),
    onSettled: () => setBusy(false),
  });

  const startPreview = useMutation({
    mutationFn: () => api.studioPreview(workspaceId),
    onMutate: () => setBusy(true),
    onSuccess: (result) => {
      setPreview(result);
      if (result.ok) toast.success('Preview is live');
      else toast.error(result.message || 'The preview did not answer');
    },
    onError: (error) => toast.error(errorText(error, 'The preview could not start')),
    onSettled: () => setBusy(false),
  });

  const stopPreview = useMutation({
    mutationFn: () => api.studioStopPreview(workspaceId),
    onSuccess: (result) => {
      setPreview(null);
      toast.success(
        `Stopped${result.closed_ports.length ? ` (closed ${result.closed_ports.join(', ')})` : ''}`,
      );
    },
    onError: (error) => toast.error(errorText(error, 'The preview could not be stopped')),
  });

  const makeArt = useMutation({
    mutationFn: () => api.studioMakeImage(workspaceId, artPrompt.trim(), artName.trim() || undefined),
    onMutate: () => setBusy(true),
    onSuccess: (asset) => {
      toast.success(`Saved ${asset.path}`);
      queryClient.invalidateQueries({ queryKey: ['studio', 'assets', workspaceId] });
    },
    onError: (error) => toast.error(errorText(error, 'The picture could not be made')),
    onSettled: () => setBusy(false),
  });

  const buildDeck = useMutation({
    mutationFn: () => api.studioBuildDeck(workspaceId, deckBody, deckTitle.trim()),
    onMutate: () => setBusy(true),
    onSuccess: (built) => toast.success(`Wrote ${built.path} (${built.slides} slides)`),
    onError: (error) => toast.error(errorText(error, 'The deck could not be built')),
    onSettled: () => setBusy(false),
  });

  const scaffoldGame = useMutation({
    mutationFn: () => api.studioScaffoldGame(workspaceId, gameTitle.trim()),
    onMutate: () => setBusy(true),
    onSuccess: (built) => toast.success(`Wrote ${built.path}`),
    onError: (error) => toast.error(errorText(error, 'The game could not be created')),
    onSettled: () => setBusy(false),
  });

  const verify = useMutation({
    mutationFn: () => api.studioVerify(workspaceId),
    onMutate: () => setBusy(true),
    onSuccess: (result) => {
      setVerdict(result);
      if (result.ok) toast.success('The workspace works');
      else toast.error(result.hint);
    },
    onError: (error) => toast.error(errorText(error, 'The check could not run')),
    onSettled: () => setBusy(false),
  });

  const inspectModel = useMutation({
    mutationFn: () => api.stlInspect(workspaceId, modelPath.trim()),
    onMutate: () => {
      setBusy(true);
      setEdit(null);
    },
    onSuccess: (summary) => {
      setModel(summary);
      toast.success(stlVerdict(summary));
    },
    onError: (error) => toast.error(errorText(error, 'The model could not be read')),
    onSettled: () => setBusy(false),
  });

  const editModel = useMutation({
    // `fit` only when a size was typed; `center` always, so the button is never a no-op.
    mutationFn: () =>
      api.stlTransform(workspaceId, modelPath.trim(), {
        ...(Number(fitMm) > 0 ? { fit: Number(fitMm) } : {}),
        center: true,
      }),
    onMutate: () => setBusy(true),
    onSuccess: (result) => {
      setEdit(result);
      setModel(result.after);
      toast.success(`Wrote ${result.path}`);
    },
    onError: (error) => toast.error(errorText(error, 'The model could not be edited')),
    onSettled: () => setBusy(false),
  });

  const state = previewState(preview);
  const field =
    'w-full rounded-lg border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white placeholder:text-slate-600';


  return (
    <div className="flex flex-col gap-3" data-testid="workspace-studio">
      <div className="rounded-xl border border-white/10 bg-black/20 p-2.5">
        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Studio</p>
        <p className="mt-1 text-xs text-slate-300" data-testid="studio-stack">
          {stackLine(profileQuery.data)}
        </p>
        {profileQuery.data?.stack === 'unknown' ? (
          <p className="mt-1 text-[11px] text-slate-500">
            A deck, a game or a folder of documents works without a project file.
          </p>
        ) : null}

        <div className="mt-2 flex flex-wrap gap-1.5">
          {steps.map((step) => (
            <button
              key={step.name}
              onClick={() => run.mutate(step.name)}
              disabled={busy}
              title={step.command}
              className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-slate-300 hover:bg-white/10 disabled:opacity-40"
            >
              {step.name}
            </button>
          ))}
          <button
            onClick={() => startPreview.mutate()}
            disabled={busy || profileQuery.data?.stack === 'unknown'}
            className="flex items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-emerald-300 disabled:opacity-40"
          >
            <Play size={11} /> preview
          </button>
          {state.live ? (
            <button
              onClick={() => stopPreview.mutate()}
              className="flex items-center gap-1 rounded-lg border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-rose-300"
            >
              <Square size={11} /> stop
            </button>
          ) : null}
        </div>

        {state.live ? (
          <a
            href={state.label}
            target="_blank"
            rel="noreferrer"
            className="mt-2 flex items-center gap-1 text-[11px] text-emerald-300 underline decoration-dotted"
          >
            <ExternalLink size={11} /> {state.label}
          </a>
        ) : preview ? (
          <p className="mt-2 text-[11px] text-amber-300">{state.label}</p>
        ) : null}
      </div>

      <div className="rounded-xl border border-white/10 bg-black/20 p-2.5">
        <p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-500">
          <Stethoscope size={11} /> Check
        </p>
        <p className="mt-1 text-[11px] text-slate-500">
          Proves the shell, a real program and the tests, in that order.
        </p>
        <button
          onClick={() => verify.mutate()}
          disabled={busy}
          className="mt-1.5 rounded-lg border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-sky-300 disabled:opacity-40"
        >
          run check
        </button>
        {verdict ? (
          <p
            className={`mt-1.5 text-[11px] ${verdict.ok ? 'text-emerald-300' : 'text-rose-300'}`}
            data-testid="studio-verdict"
          >
            {verifyLine(verdict)}
          </p>
        ) : null}
        {verdict && !verdict.shell.ok ? (
          <button
            onClick={() => verify.reset()}
            className="mt-1 text-[10px] text-slate-500 underline decoration-dotted"
          >
            clear
          </button>
        ) : null}
      </div>

      <div className="rounded-xl border border-white/10 bg-black/20 p-2.5">
        <p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-500">
          <Hammer size={11} /> 3D model (STL)
        </p>
        <input
          value={modelPath}
          onChange={(event) => setModelPath(event.target.value)}
          placeholder="parts/bracket.stl"
          aria-label="model path"
          className={`mt-1.5 ${field}`}
        />
        <div className="mt-1.5 flex gap-1.5">
          <input
            value={fitMm}
            onChange={(event) => setFitMm(event.target.value)}
            inputMode="decimal"
            placeholder="longest side (mm)"
          aria-label="longest side in millimetres"
            className={field}
          />
          <button
            onClick={() => inspectModel.mutate()}
            disabled={busy || !modelPath.trim()}
            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-slate-300 disabled:opacity-40"
          >
            read
          </button>
          <button
            onClick={() => editModel.mutate()}
            disabled={busy || !modelPath.trim()}
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-amber-300 disabled:opacity-40"
          >
            edit
          </button>
        </div>
        {model ? (
          <p className="mt-1.5 text-[11px] text-slate-300" data-testid="studio-model">
            {stlSizeLine(model)} - {stlVerdict(model)}
          </p>
        ) : null}
        {edit ? (
          <p className="mt-1 text-[11px] text-emerald-300" data-testid="studio-model-edit">
            {stlEditLine(edit)} - saved to {edit.path}
          </p>
        ) : null}
        {model?.warnings?.length ? (
          <ul className="mt-1 list-disc pl-4 text-[10px] text-amber-300">
            {model.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {output ? (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl border border-white/10 bg-black/40 p-2 text-[11px] text-slate-300">
          {output}
        </pre>
      ) : null}

      <div className="rounded-xl border border-white/10 bg-black/20 p-2.5">
        <p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-500">
          <ImageIcon size={11} /> Art
        </p>
        <input
          value={artPrompt}
          onChange={(event) => setArtPrompt(event.target.value)}
          placeholder="a friendly blue robot, flat cartoon, transparent background"
          aria-label="art prompt"
          className={`mt-1.5 ${field}`}
        />
        <div className="mt-1.5 flex gap-1.5">
          <input
            value={artName}
            onChange={(event) => setArtName(event.target.value)}
            placeholder="name (hero)"
          aria-label="asset name"
            className={field}
          />
          <button
            onClick={() => makeArt.mutate()}
            disabled={busy || !artPrompt.trim()}
            className="flex shrink-0 items-center gap-1 rounded-lg bg-indigo-600 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-white disabled:opacity-40"
          >
            <Sparkles size={11} /> make
          </button>
        </div>
        {(assetsQuery.data?.assets ?? []).length ? (
          <ul className="mt-1.5 space-y-0.5">
            {(assetsQuery.data?.assets ?? []).slice(-4).map((asset) => (
              <li key={asset.path} className="truncate text-[11px] text-slate-400" title={asset.prompt}>
                {asset.path}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="rounded-xl border border-white/10 bg-black/20 p-2.5">
        <p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-500">
          <Hammer size={11} /> Deck &amp; game
        </p>
        <input
          value={deckTitle}
          onChange={(event) => setDeckTitle(event.target.value)}
          placeholder="deck title"
          aria-label="deck title"
          className={`mt-1.5 ${field}`}
        />
        <textarea
          aria-label="deck markdown"
          value={deckBody}
          onChange={(event) => setDeckBody(event.target.value)}
          rows={4}
          className={`mt-1.5 ${field} font-mono`}
        />
        <button
          onClick={() => buildDeck.mutate()}
          disabled={busy || !deckBody.trim()}
          className="mt-1.5 flex w-full items-center justify-center gap-1 rounded-lg border border-white/10 bg-white/5 py-1.5 text-[10px] font-black uppercase tracking-wider text-slate-300 hover:bg-white/10 disabled:opacity-40 pointer-coarse:min-h-11"
        >
          <Wand2 size={11} /> build slides
        </button>
        <div className="mt-1.5 flex gap-1.5">
          <input
            value={gameTitle}
            onChange={(event) => setGameTitle(event.target.value)}
            placeholder="game title"
          aria-label="game title"
            className={field}
          />
          <button
            onClick={() => scaffoldGame.mutate()}
            disabled={busy || !gameTitle.trim()}
            className="shrink-0 rounded-lg border border-purple-500/30 bg-purple-500/10 px-2 py-1 text-[10px] font-black uppercase tracking-wider text-purple-300 disabled:opacity-40"
          >
            new game
          </button>
        </div>
      </div>
    </div>
  );
}

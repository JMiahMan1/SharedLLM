import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Activity, Download } from 'lucide-react';
import toast from 'react-hot-toast';
import Modal from '../ui/Modal';
import { api } from '../../services/api';
import { artifactKind, downloadBlobUrl } from '../../lib/artifactKinds';
import { errorText } from '../../lib/errorText';
import { downloadMissionLog, parseMissionLog } from '../../lib/missionLog';
import { MissionTimeline } from './MissionTimeline';

const RUNNING = ['executing', 'running', 'scheduled', 'queued'];

const formatDuration = (secs: number | null | undefined) =>
  secs == null ? '—' : `${Math.floor(secs / 60)}m ${secs % 60}s`;

interface RavenMissionDetailProps {
  missionId: number | null;
  onClose: () => void;
  /** Called with the new mission's id after a refinement is queued. */
  onRefined?: (missionId: number) => void;
}

/**
 * One mission: what it was asked, what it did, what it produced, and a box to
 * send it follow-up instructions. The full record (log, result) is loaded
 * here, by id, rather than trusted from a list row.
 */
export default function RavenMissionDetail({ missionId, onClose, onRefined }: RavenMissionDetailProps) {
  const [refinePrompt, setRefinePrompt] = useState('');
  const [now, setNow] = useState(() => Date.now());

  const { data: mission, isLoading, isError } = useQuery({
    queryKey: ['raven-mission', missionId],
    queryFn: () => api.getRavenMission(missionId as number),
    enabled: missionId != null,
    refetchInterval: (q) => (RUNNING.includes(q.state.data?.status ?? '') ? 4000 : false),
  });
  const running = RUNNING.includes(mission?.status ?? '');

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);

  const refine = useMutation({
    mutationFn: ({ id, prompt }: { id: number; prompt: string }) => api.refineRavenMission(id, prompt),
    onSuccess: (resp) => {
      toast.success('Refinement queued');
      setRefinePrompt('');
      onClose();
      if (resp?.mission_id) onRefined?.(resp.mission_id);
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to refine mission')),
  });

  const close = () => {
    setRefinePrompt('');
    onClose();
  };

  const events = parseMissionLog(mission?.output_log);
  const seconds = running && mission?.started_at
    ? Math.max(0, Math.floor((now - new Date(mission.started_at).getTime()) / 1000))
    : mission?.duration;

  return (
    <Modal isOpen={missionId != null} onClose={close} title={`Mission #${missionId ?? ''}`} size="3xl">
      <div className="overflow-y-auto p-4 sm:p-6 space-y-5 text-left">
        {isLoading && <p className="text-sm text-slate-500">Loading mission…</p>}
        {isError && <p className="text-sm text-red-400">This mission could not be loaded.</p>}
        {mission && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-white/5 p-4 rounded-xl border border-white/5 text-xs">
              <div>
                <span className="text-slate-500 uppercase font-black text-[10px] block">Status</span>
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold inline-block uppercase mt-1 ${
                  mission.status === 'completed' ? 'bg-emerald-500/20 text-emerald-300' :
                  mission.status === 'failed' ? 'bg-red-500/20 text-red-300' : 'bg-slate-500/20 text-slate-300'
                }`}>
                  {mission.status}
                </span>
              </div>
              <div className="min-w-0">
                <span className="text-slate-500 uppercase font-black text-[10px] block">Container</span>
                <span className="text-white font-semibold block mt-1 truncate">{mission.target_container || 'System'}</span>
              </div>
              <div>
                <span className="text-slate-500 uppercase font-black text-[10px] block">Duration</span>
                <span className="text-white font-semibold block mt-1">
                  {formatDuration(seconds)}
                  {running && <span className="ml-1 text-[10px] text-emerald-400 animate-pulse">live</span>}
                </span>
              </div>
              <div className="min-w-0">
                <span className="text-slate-500 uppercase font-black text-[10px] block">Workspace</span>
                <span className="text-blue-300 font-mono block mt-1 truncate">{mission.workspace_id || '—'}</span>
              </div>
            </div>

            <Section label="Mission">
              <div className="bg-black/30 border border-white/5 p-3 rounded-lg text-xs text-slate-300 max-h-32 overflow-y-auto whitespace-pre-wrap break-words">
                {mission.proposed_mission}
              </div>
            </Section>

            {mission.result && (
              <Section label="Result">
                <div className="bg-black/30 border border-white/5 p-3 rounded-lg text-xs text-emerald-300 max-h-40 overflow-y-auto whitespace-pre-wrap break-words">
                  {mission.result}
                </div>
              </Section>
            )}

            {mission.last_llm_reply && (
              <Section label="Last model reply">
                <div className="bg-black/30 border border-white/5 p-3 rounded-lg text-xs text-slate-300 max-h-40 overflow-y-auto whitespace-pre-wrap break-words">
                  {mission.last_llm_reply}
                </div>
              </Section>
            )}

            {mission.workspace_id && (
              <Section label="Files in the workspace">
                <MissionArtifacts workspaceId={mission.workspace_id} />
              </Section>
            )}

            <Section
              label="Timeline"
              action={
                <button
                  onClick={() => downloadMissionLog(mission.id, events, mission.result)}
                  className="inline-flex items-center gap-1.5 min-h-11 px-3 rounded-lg text-[10px] font-black uppercase tracking-widest text-slate-300 hover:bg-white/10"
                >
                  <Download size={12} /> Download log
                </button>
              }
            >
              <div className="max-h-64 overflow-y-auto bg-black/40 p-3 rounded-lg border border-white/5">
                <MissionTimeline events={events} />
              </div>
            </Section>

            <div className="pt-4 border-t border-white/5">
              <label htmlFor="raven-refine" className="text-xs font-bold text-slate-300 mb-1 flex items-center gap-1.5">
                <Activity size={14} className="text-indigo-400" />
                Follow-up instructions
              </label>
              <p className="text-[11px] text-slate-500 mb-2">
                Raven runs again in the same workspace with this mission&rsquo;s history.
              </p>
              <div className="flex flex-col sm:flex-row gap-2">
                <textarea
                  id="raven-refine"
                  value={refinePrompt}
                  onChange={(e) => setRefinePrompt(e.target.value)}
                  placeholder="e.g. Fix the failing test and push again"
                  rows={2}
                  disabled={refine.isPending}
                  className="glass-input flex-1 text-sm resize-none"
                />
                <button
                  onClick={() => refine.mutate({ id: mission.id, prompt: refinePrompt })}
                  disabled={!refinePrompt.trim() || refine.isPending}
                  className="glass-button min-h-11 px-4 text-[10px] font-black uppercase tracking-widest text-indigo-300 disabled:opacity-50"
                >
                  {refine.isPending ? 'Sending…' : 'Refine'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

function Section({ label, action, children }: { label: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-[10px] uppercase font-black text-slate-500 tracking-wider">{label}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

const PREVIEW_KINDS = new Set(['audio', 'video', 'image', 'pdf', 'text']);
const MAX_PREVIEW_BYTES = 25 * 1024 * 1024;

function MissionArtifacts({ workspaceId }: { workspaceId: string }) {
  const [entries, setEntries] = useState<{ path: string; size: number }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blobs, setBlobs] = useState<Record<string, { url: string; text?: string }>>({});

  useEffect(() => {
    let cancelled = false;
    api
      .listWorkspaceFiles(workspaceId, '.', true, 4)
      .then((res) => {
        if (cancelled) return;
        setEntries((Array.isArray(res?.entries) ? res.entries : []).filter((f) => !f.is_dir).map((f) => ({ path: f.path ?? f.name, size: f.size ?? 0 })));
      })
      .catch((e) => {
        if (!cancelled) setError(errorText(e, 'Could not list the workspace'));
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  // Blob URLs hold the file in memory until revoked: release them on close.
  const urls = useRef<string[]>([]);
  useEffect(() => () => urls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const loadBlob = useCallback(
    async (path: string, kind: string) => {
      try {
        const blob = await api.fetchWorkspaceFileRaw(workspaceId, path);
        const url = URL.createObjectURL(blob);
        urls.current.push(url);
        const text = kind === 'text' ? await blob.text() : undefined;
        setBlobs((prev) => ({ ...prev, [path]: { url, text } }));
        return url;
      } catch (e) {
        toast.error(`Could not load ${path}: ${errorText(e)}`);
        return null;
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    if (!entries) return;
    // Preview small files only; large ones load when downloaded.
    entries
      .filter((e) => PREVIEW_KINDS.has(artifactKind(e.path)) && e.size <= MAX_PREVIEW_BYTES)
      .forEach((e) => void loadBlob(e.path, artifactKind(e.path)));
  }, [entries, loadBlob]);

  const download = async (path: string) => {
    const url = blobs[path]?.url ?? (await loadBlob(path, artifactKind(path)));
    if (url) downloadBlobUrl(url, path);
  };

  if (error) return <p className="text-red-400 text-xs p-2">{error}</p>;
  if (!entries) return <p className="text-slate-500 text-xs p-2">Loading files…</p>;
  if (entries.length === 0) return <p className="text-slate-500 text-xs p-2">No files in this workspace.</p>;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {entries.map((e) => {
        const kind = artifactKind(e.path);
        const b = blobs[e.path];
        return (
          <div key={e.path} className="bg-black/30 border border-white/5 rounded-lg p-3 space-y-2 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-slate-300 font-mono truncate" title={e.path}>{e.path.split('/').pop()}</span>
              <div className="flex items-center gap-1.5 shrink-0">
                <span className="text-[10px] text-slate-500">{formatBytes(e.size)}</span>
                <button
                  onClick={() => void download(e.path)}
                  className="min-h-11 min-w-11 flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/10 rounded"
                  aria-label={`Download ${e.path}`}
                >
                  <Download size={14} />
                </button>
              </div>
            </div>
            {kind === 'audio' && b?.url && <audio controls src={b.url} className="w-full" />}
            {kind === 'video' && b?.url && <video controls src={b.url} className="w-full max-h-56 rounded bg-black" />}
            {kind === 'image' && b?.url && <img src={b.url} alt={e.path} className="max-h-56 max-w-full object-contain rounded bg-black/40 mx-auto" />}
            {kind === 'pdf' && b?.url && <iframe src={b.url} title={e.path} className="w-full h-48 rounded bg-white/5 border border-white/10" />}
            {kind === 'text' && b?.text != null && <pre className="text-[10px] text-slate-300 bg-black/40 p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap">{b.text.slice(0, 2000)}</pre>}
          </div>
        );
      })}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

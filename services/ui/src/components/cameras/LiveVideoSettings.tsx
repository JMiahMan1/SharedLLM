import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, RefreshCw, Save } from 'lucide-react';
import { errorText } from '../../lib/errorText';
import {
  getLiveVideoSettings, saveLiveVideoSettings, syncLiveVideo,
  type LiveQuality, type LiveVideoCamera, type LiveVideoChange, type LiveVideoSettings as Settings,
} from '../../lib/cameraLive';

const QUERY_KEY = ['live-video-settings'];
const FIELD = 'min-h-11 w-full rounded-xl bg-white/5 border border-white/10 px-3 text-sm text-white';
type CameraSettings = LiveVideoCamera['settings'];

function statusLine(s: Settings): string {
  if (!s.configured) return 'Not set up yet.';
  if (!s.enabled) return 'Turned off.';
  if (s.state === 'loaded') return `Running${s.server.version ? ` (go2rtc ${s.server.version})` : ''}.`;
  return s.reason ? `Not running: ${s.reason}` : 'Starting…';
}

/**
 * Cameras > Live video settings (admins): the go2rtc integration that gives the
 * cameras real-time video. Its server, the WebRTC address and port phones
 * connect to, and per camera the quality, whether it streams only while
 * someone watches, and whether to leave it out.
 */
export default function LiveVideoSettings() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: QUERY_KEY, queryFn: getLiveVideoSettings, retry: false });
  const [form, setForm] = useState<LiveVideoChange>({});
  const [cams, setCams] = useState<Record<string, CameraSettings>>({});

  useEffect(() => {
    const s = settings.data;
    if (!s) return;
    // A fresh read from the hub replaces whatever was typed.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the form mirrors the server
    setForm({ enabled: s.configured ? s.enabled : true, url: s.url, webrtc_host: s.options.webrtc_host,
      webrtc_port: s.options.webrtc_port, default_quality: s.options.default_quality });
    setCams(Object.fromEntries(s.cameras.map((c) => [c.entity_id, { ...c.settings }])));
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () => saveLiveVideoSettings({ ...form, cameras: cams }),
    onSuccess: (data) => { queryClient.setQueryData(QUERY_KEY, data); toast.success('Live video settings saved'); },
    onError: (err) => toast.error(errorText(err, 'Couldn’t save the live video settings')),
  });
  const sync = useMutation({
    mutationFn: syncLiveVideo,
    onSuccess: (data) => queryClient.setQueryData(QUERY_KEY, data),
    onError: (err) => toast.error(errorText(err, 'Couldn’t reach go2rtc')),
  });

  if (settings.isLoading) {
    return <div className="flex justify-center py-6" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>;
  }
  if (settings.isError || !settings.data) {
    return <p className="text-sm text-red-300">{errorText(settings.error, 'Couldn’t read the live video settings')}</p>;
  }
  const s = settings.data;
  const setCam = (id: string, patch: CameraSettings) => setCams((all) => ({ ...all, [id]: { ...all[id], ...patch } }));

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-400">{statusLine(s)} {s.server.error ? `(${s.server.error})` : ''}</p>
      <label className="flex min-h-11 items-center gap-3 text-sm text-white">
        <input type="checkbox" checked={Boolean(form.enabled)} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
        Real-time video (go2rtc)
      </label>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-slate-400 sm:col-span-3">go2rtc API
          <input className={FIELD} value={form.url ?? ''} onChange={(e) => setForm({ ...form, url: e.target.value })} />
        </label>
        <label className="text-xs text-slate-400">WebRTC address (blank: automatic)
          <input className={FIELD} value={form.webrtc_host ?? ''} placeholder="automatic"
            onChange={(e) => setForm({ ...form, webrtc_host: e.target.value })} />
        </label>
        <label className="text-xs text-slate-400">WebRTC port
          <input className={FIELD} type="number" min={1024} max={65535} value={form.webrtc_port ?? 8555}
            onChange={(e) => setForm({ ...form, webrtc_port: Number(e.target.value) })} />
        </label>
        <label className="text-xs text-slate-400">Default quality
          <select className={FIELD} value={form.default_quality ?? 'sub'}
            onChange={(e) => setForm({ ...form, default_quality: e.target.value as LiveQuality })}>
            <option value="sub">SD (sub stream)</option>
            <option value="main">HD (main stream)</option>
          </select>
        </label>
      </div>

      {s.cameras.length > 0 && (
        <ul className="space-y-2" aria-label="Cameras">
          {s.cameras.map((c) => {
            const cam = cams[c.entity_id] ?? {};
            return (
              <li key={c.entity_id} className="rounded-xl border border-white/10 bg-white/5 p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm text-white">{c.name}</span>
                  <span className="text-[11px] text-slate-400">
                    {c.registered.length ? `live: ${c.registered.map((q) => (q === 'main' ? 'HD' : 'SD')).join(', ')}` : 'snapshots only'}
                  </span>
                </div>
                {c.note && <p className="text-[11px] text-slate-500">{c.note}</p>}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-300">
                  <select className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-2 text-xs text-white"
                    aria-label={`${c.name} quality`} value={cam.quality ?? ''}
                    onChange={(e) => setCam(c.entity_id, { quality: (e.target.value || undefined) as LiveQuality | undefined })}>
                    <option value="">Default quality</option>
                    <option value="sub">SD</option>
                    <option value="main">HD</option>
                  </select>
                  <label className="flex min-h-11 items-center gap-2">
                    <input type="checkbox" checked={cam.on_demand !== false} disabled={Boolean(c.battery)}
                      onChange={(e) => setCam(c.entity_id, { on_demand: e.target.checked })} />
                    Only while watched{c.battery ? ' (battery camera)' : ''}
                  </label>
                  {c.platform === 'mjpeg' && (
                    <label className="flex min-h-11 items-center gap-2">
                      <input type="checkbox" checked={Boolean(cam.transcode)}
                        onChange={(e) => setCam(c.entity_id, { transcode: e.target.checked })} />
                      Convert to video (uses CPU)
                    </label>
                  )}
                  <label className="flex min-h-11 items-center gap-2">
                    <input type="checkbox" checked={Boolean(cam.disabled)}
                      onChange={(e) => setCam(c.entity_id, { disabled: e.target.checked })} />
                    Leave out
                  </label>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending}
          className="min-h-11 flex items-center gap-2 rounded-xl bg-cyan-500/20 border border-cyan-500/40 px-4 text-sm text-cyan-100 disabled:opacity-50">
          {save.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}Save
        </button>
        {s.configured && s.enabled && (
          <button type="button" onClick={() => sync.mutate()} disabled={sync.isPending}
            className="min-h-11 flex items-center gap-2 rounded-xl bg-white/5 border border-white/10 px-4 text-sm text-white disabled:opacity-50">
            <RefreshCw size={16} className={sync.isPending ? 'animate-spin' : ''} />Re-register cameras
          </button>
        )}
      </div>
      <p className="text-[11px] text-slate-500">
        Phones and browsers on the home network play WebRTC from the WebRTC port; elsewhere the video comes
        through the app instead, a second or so behind.
      </p>
    </div>
  );
}

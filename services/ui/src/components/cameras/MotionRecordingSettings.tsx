import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, Save } from 'lucide-react';
import { errorText } from '../../lib/errorText';
import { Toggle } from '../ui/Toggle';
import {
  cameraStatusLine, changedCameras, getMotionRecordingSettings, recordableCameras, recorderStatusLine,
  saveMotionRecordingSettings, sensitivityLabel,
  type CameraRecordingSettings, type MotionRecordingChange, type MotionRecordingSettings, type RecordingCamera,
  type RecordingNumber,
} from '../../lib/cameraRecording';

const QUERY_KEY = ['motion-recording-settings'];
const choice = (on: boolean) => `min-h-11 min-w-0 rounded-xl border px-3 py-2 text-left text-white ${
  on ? 'bg-cyan-500/15 border-cyan-500/40' : 'bg-white/5 border-white/10 hover:bg-white/10'}`;
const FIELD = 'min-h-11 w-full rounded-xl bg-white/5 border border-white/10 px-3 text-sm text-white';

const NUMBERS: { key: RecordingNumber; label: string }[] = [
  { key: 'pre_s', label: 'Seconds before motion' },
  { key: 'post_s', label: 'Seconds after motion' },
  { key: 'max_s', label: 'Longest clip (seconds)' },
  { key: 'fps', label: 'Pictures a second' },
  { key: 'retention_days', label: 'Keep clips (days)' },
];

interface AccountForm {
  account: 'household' | 'custom';
  url: string;
  username: string;
  password: string;
  folder: string;
  enabled: boolean;
}

function CameraCard({ camera, value, limits, onChange }: {
  camera: RecordingCamera;
  value: CameraRecordingSettings;
  limits: MotionRecordingSettings['limits'];
  onChange: (patch: Partial<CameraRecordingSettings>) => void;
}) {
  const blocked = Boolean(camera.unavailable_reason);
  const id = camera.entity_id.replace(/\W/g, '-');
  return (
    <li className="rounded-xl border border-white/10 bg-white/5 p-3 space-y-3" data-testid="recording-camera">
      <div className="flex min-h-11 items-center gap-3 text-sm text-white">
        <span className="min-w-0 flex-1 truncate">{camera.name}</span>
        <Toggle checked={value.enabled && !blocked} disabled={blocked} ariaLabel={`Record motion on ${camera.name}`}
          onChange={(on) => onChange({ enabled: on })} />
      </div>
      <p className="text-[11px] text-slate-400">
        {blocked ? `Can’t record: ${camera.unavailable_reason}.` : cameraStatusLine({ ...camera, settings: value })}
      </p>
      {value.enabled && !blocked && (
        <div className="space-y-3">
          <label className="block text-xs text-slate-400" htmlFor={`${id}-sens`}>
            Sensitivity: {value.sensitivity} ({sensitivityLabel(value.sensitivity)})
          </label>
          <input id={`${id}-sens`} type="range" className="min-h-11 w-full accent-cyan-400"
            min={limits.sensitivity[0]} max={limits.sensitivity[1]} value={value.sensitivity}
            onChange={(e) => onChange({ sensitivity: Number(e.target.value) })} />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {NUMBERS.map(({ key, label }) => (
              <label key={key} className="text-xs text-slate-400">{label}
                <input className={FIELD} type="number" inputMode="numeric" min={limits[key][0]} max={limits[key][1]}
                  value={value[key]} onChange={(e) => onChange({ [key]: Number(e.target.value) })} />
              </label>
            ))}
          </div>
          <div className="space-y-2">
            <div className="flex min-h-11 items-center gap-3 text-xs text-slate-300">
              <span className="flex-1">Only record at certain times</span>
              <Toggle checked={Boolean(value.schedule)} ariaLabel="Only record at certain times"
                onChange={(on) => onChange({ schedule: on ? { from: '22:00', to: '06:00' } : null })} />
            </div>
            {value.schedule && (
              <div className="grid grid-cols-2 gap-3">
                <label className="text-xs text-slate-400">From
                  <input className={`${FIELD} [color-scheme:dark]`} type="time" value={value.schedule.from}
                    onChange={(e) => onChange({ schedule: { ...value.schedule!, from: e.target.value } })} />
                </label>
                <label className="text-xs text-slate-400">To
                  <input className={`${FIELD} [color-scheme:dark]`} type="time" value={value.schedule.to}
                    onChange={(e) => onChange({ schedule: { ...value.schedule!, to: e.target.value } })} />
                </label>
              </div>
            )}
          </div>
          {camera.motion_entity && (
            <p className="text-[11px] text-slate-500">
              Motion sensor for automations: <code className="text-slate-300">{camera.motion_entity}</code>
            </p>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Cameras > Motion recording (admins): for cameras that cannot record
 * themselves, the hub watches the picture for motion and keeps the clips in
 * Nextcloud. The account, the folder, and per camera the same choices a Reolink
 * camera has: sensitivity, time kept before and after, clip length, schedule,
 * and how long clips are kept.
 */
export default function MotionRecordingSettings() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: QUERY_KEY, queryFn: getMotionRecordingSettings, retry: false });
  const [form, setForm] = useState<AccountForm>({
    account: 'household', url: '', username: '', password: '', folder: '', enabled: true,
  });
  const [cams, setCams] = useState<Record<string, CameraRecordingSettings>>({});

  useEffect(() => {
    const s = settings.data;
    if (!s) return;
    // A fresh read from the hub replaces whatever was typed.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the form mirrors the server
    setForm({
      account: s.account ?? (s.household_account ? 'household' : 'custom'),
      url: s.url, username: s.username, password: '', folder: s.folder, enabled: s.configured ? s.enabled : true,
    });
    setCams(Object.fromEntries(s.cameras.map((c) => [c.entity_id, { ...c.settings }])));
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () => {
      const s = settings.data!;
      const change: MotionRecordingChange = { enabled: form.enabled, folder: form.folder,
        cameras: changedCameras(s.cameras, cams) };
      if (form.account === 'household') {
        if (s.account !== 'household') change.account = 'household';
      } else {
        Object.assign(change, { account: 'custom', url: form.url, username: form.username });
        if (form.password) change.password = form.password;
      }
      return saveMotionRecordingSettings(change);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(QUERY_KEY, data);
      void queryClient.invalidateQueries({ queryKey: ['cameras'] });  // a camera may now have a Recordings tab
      toast.success('Motion recording saved');
    },
    onError: (err) => toast.error(errorText(err, 'Couldn’t save motion recording')),
  });

  if (settings.isLoading) {
    return <div className="flex justify-center py-6" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>;
  }
  if (settings.isError || !settings.data) {
    return <p className="text-sm text-red-300">{errorText(settings.error, 'Couldn’t read the motion recording settings')}</p>;
  }
  const s = settings.data;
  const cameras = recordableCameras(s);
  const own = s.cameras.filter((c) => c.records_itself);
  const setCam = (eid: string, patch: Partial<CameraRecordingSettings>) =>
    setCams((all) => ({ ...all, [eid]: { ...(all[eid] ?? s.defaults), ...patch } }));

  return (
    <div className="space-y-4" data-testid="motion-recording-settings">
      <p className="text-xs text-slate-400">{recorderStatusLine(s)}</p>
      <div className="flex min-h-11 items-center gap-3 text-sm text-white">
        <span className="flex-1">Record motion for cameras without their own storage</span>
        <Toggle checked={form.enabled} ariaLabel="Motion recording" onChange={(on) => setForm({ ...form, enabled: on })} />
      </div>

      <fieldset className="space-y-2">
        <legend className="text-xs text-slate-400">Where clips are kept (Nextcloud)</legend>
        <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Nextcloud account">
          {s.household_account && (
            <button type="button" aria-pressed={form.account === 'household'}
              onClick={() => setForm({ ...form, account: 'household' })} className={choice(form.account === 'household')}>
              <span className="block text-sm">Jarvis’s Nextcloud</span>
              <span className="block truncate text-[11px] text-slate-400">
                {s.household_account.username} at {s.household_account.url}
              </span>
            </button>
          )}
          <button type="button" aria-pressed={form.account === 'custom'}
            onClick={() => setForm({ ...form, account: 'custom' })} className={choice(form.account === 'custom')}>
            <span className="block text-sm">Another Nextcloud account</span>
          </button>
        </div>
        {form.account === 'custom' && (
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-xs text-slate-400 sm:col-span-3">Address
              <input className={FIELD} value={form.url} placeholder="https://cloud.example.com"
                onChange={(e) => setForm({ ...form, url: e.target.value })} />
            </label>
            <label className="text-xs text-slate-400">User
              <input className={FIELD} value={form.username} autoComplete="off"
                onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </label>
            <label className="text-xs text-slate-400 sm:col-span-2">App password
              <input className={FIELD} type="password" value={form.password} autoComplete="new-password"
                placeholder={s.has_password && s.account === 'custom' ? 'Saved; type to change' : ''}
                onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </label>
          </div>
        )}
        <label className="block text-xs text-slate-400">Folder
          <input className={FIELD} value={form.folder} onChange={(e) => setForm({ ...form, folder: e.target.value })} />
        </label>
      </fieldset>

      {cameras.length > 0 ? (
        <ul className="space-y-2" aria-label="Cameras to record">
          {cameras.map((c) => (
            <CameraCard key={c.entity_id} camera={c} value={cams[c.entity_id] ?? c.settings} limits={s.limits}
              onChange={(patch) => setCam(c.entity_id, patch)} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-400">Every camera keeps its own recordings.</p>
      )}
      {own.length > 0 && (
        <p className="text-[11px] text-slate-500">
          Record on their own (their settings are on the camera): {own.map((c) => c.name).join(', ')}.
        </p>
      )}

      <button type="button" onClick={() => save.mutate()} disabled={save.isPending}
        className="min-h-11 flex items-center gap-2 rounded-xl bg-cyan-500/20 border border-cyan-500/40 px-4 text-sm text-cyan-100 disabled:opacity-50">
        {save.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}Save
      </button>
      <p className="text-[11px] text-slate-500">
        Each clip starts a few seconds before the motion and shows in the camera’s Recordings tab. Motion is seen
        by comparing pictures, so lights switching on are ignored; a camera’s motion sensor can drive automations.
      </p>
    </div>
  );
}

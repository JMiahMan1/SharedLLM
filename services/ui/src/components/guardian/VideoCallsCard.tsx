import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Ban, ScanEye, Video } from 'lucide-react';
import toast from 'react-hot-toast';
import Toggle from '../ui/Toggle';
import MemberCard from '../member/MemberCard';
import { api } from '../../services/api';
import { statusQuery } from '../../lib/memberPage';
import { errorText } from '../../lib/errorText';
import { captureProblem, parseList, withVideoCalls } from '../../lib/videoCalls';
import type { VideoCallRules } from '../../types/api';

const INTERVALS = [15, 30, 45, 60, 90, 120, 300];
const FRAME_CAPS = [5, 10, 20, 40, 60];
const CORROBORATE = [2, 3, 4, 5];

function seconds(s: number): string {
  return s < 60 ? `${s} s` : `${s / 60} min`;
}

const SELECT =
  'min-h-11 w-full rounded-lg border border-white/10 bg-black/20 px-2 text-xs text-slate-100 focus:border-sky-300/40 focus:outline-none disabled:opacity-50';
const BUTTON =
  'inline-flex min-h-11 items-center justify-center gap-1 rounded-lg border px-3 text-xs font-semibold transition disabled:cursor-wait disabled:opacity-50';

/**
 * Video calls for one child: the checks (a downscaled screenshot of a call now
 * and then, through the photo scanner, and a parent told only when two checks
 * agree on enough frames), the one switch that blocks every video-call app and
 * site, and the lists both use (the shipped defaults until a parent edits them).
 */
export default function VideoCallsCard({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data: status, isLoading, isError } = useQuery(statusQuery(username));
  const technical = Boolean(status?.technical);
  const devices = useQuery({
    queryKey: ['member-devices', username],
    queryFn: () => api.getMemberDevices(username),
    retry: false,
    enabled: technical,
  });
  const rules = status?.rules;
  const v = status?.video_calls;
  const [apps, setApps] = useState<string | null>(null);
  const [sites, setSites] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (patch: Partial<Record<keyof VideoCallRules, unknown>>) =>
      api.setScreenRules(username, withVideoCalls(rules, patch)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
      void queryClient.invalidateQueries({ queryKey: ['screen-time-rules', username] });
      toast.success('Video call settings saved');
    },
    onError: (err) => toast.error(errorText(err, 'Could not save the video call settings')),
  });

  const problems = (devices.data?.devices || [])
    .filter((d) => d.platform !== 'esphome' && d.capabilities?.guard)
    .map((d) => {
      const g = d.capabilities.guard!;
      const problem = captureProblem(g.videoCallCapture, g.videoCallLast);
      return problem ? { key: d.device_key, device: d.label || d.model || 'A device', problem } : null;
    })
    .filter((p): p is { key: string; device: string; problem: string } => p !== null);

  const busy = save.isPending;
  const appsText = apps ?? (v?.apps || []).join('\n');
  const sitesText = sites ?? (v?.sites || []).join('\n');

  return (
    <MemberCard
      title="Video calls"
      icon={Video}
      tone="text-sky-300"
      testId="guardian-video-calls-card"
      isLoading={isLoading}
      error={isError ? 'Video call settings could not be loaded.' : null}
      empty={!isLoading && !isError && (!status?.can_manage || !v) ? 'Only their parents can change this.' : null}
    >
      {v && status?.can_manage && (
        <div className="space-y-3 text-xs">
          {problems.length > 0 && (
            <ul className="space-y-1.5" data-testid="video-calls-unsupported">
              {problems.map((p) => (
                <li key={p.key} className="flex gap-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-2.5 text-amber-200" role="status">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
                  <span className="min-w-0 break-words"><span className="font-semibold">{p.device}: </span>{p.problem}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-sky-400/20 bg-sky-400/10">
                  <ScanEye size={15} className="text-sky-300" />
                </span>
                <div className="min-w-0 space-y-1">
                  <p className="font-semibold text-white">Check video calls</p>
                  <p className="text-[11px] leading-relaxed text-slate-400">
                    While {name} is on a video call (a FaceTime link in Chrome, WhatsApp, Zoom and the rest of the list
                    below), a small downscaled screenshot is checked by your household AI now and then. You are only
                    notified when two separate checks agree on at least {v.corroborate_frames} screenshots of the same
                    call. Anything less waits in Concerns, marked "Unsure, not sent". Nothing is ever blocked on its own.
                  </p>
                </div>
              </div>
              <Toggle
                checked={v.scan}
                onChange={(next) => save.mutate({ scan: next })}
                disabled={busy}
                ariaLabel={`Check video calls for ${name}`}
                testId="toggle-video-call-scan"
              />
            </div>
            {v.scan && (
              <div className="grid grid-cols-1 gap-2 pt-1 sm:grid-cols-3">
                <label className="space-y-1">
                  <span className="text-[11px] text-slate-300">Every</span>
                  <select
                    className={SELECT}
                    value={v.interval_s}
                    disabled={busy}
                    onChange={(e) => save.mutate({ interval_s: Number(e.target.value) })}
                    data-testid="video-call-interval"
                  >
                    {Array.from(new Set([...INTERVALS, v.interval_s])).sort((a, b) => a - b).map((s) => (
                      <option key={s} value={s}>{seconds(s)}</option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-slate-300">At most, per call</span>
                  <select
                    className={SELECT}
                    value={v.max_frames}
                    disabled={busy}
                    onChange={(e) => save.mutate({ max_frames: Number(e.target.value) })}
                    data-testid="video-call-max-frames"
                  >
                    {Array.from(new Set([...FRAME_CAPS, v.max_frames])).sort((a, b) => a - b).map((n) => (
                      <option key={n} value={n}>{n} screenshots</option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-slate-300">Notify me after</span>
                  <select
                    className={SELECT}
                    value={v.corroborate_frames}
                    disabled={busy}
                    onChange={(e) => save.mutate({ corroborate_frames: Number(e.target.value) })}
                    data-testid="video-call-corroborate"
                  >
                    {Array.from(new Set([...CORROBORATE, v.corroborate_frames])).sort((a, b) => a - b).map((n) => (
                      <option key={n} value={n}>{n} confirmed screenshots</option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </div>

          <div className="flex items-start justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            <div className="flex min-w-0 items-start gap-2.5">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-red-400/20 bg-red-400/10">
                <Ban size={15} className="text-red-300" />
              </span>
              <div className="min-w-0 space-y-1">
                <p className="font-semibold text-white">Block video calls</p>
                <p className="text-[11px] leading-relaxed text-slate-400">
                  Closes every app and site in the lists below on {name}'s phone ({v.apps.length} apps,{' '}
                  {v.sites.length} sites). Sites are closed in the browser, and by the web filter when it is on.
                </p>
              </div>
            </div>
            <Toggle
              checked={v.block}
              onChange={(next) => save.mutate({ block: next })}
              disabled={busy}
              ariaLabel={`Block video calls for ${name}`}
              testId="toggle-video-call-block"
            />
          </div>

          <details className="rounded-xl border border-white/10 bg-white/[0.02] p-3" data-testid="video-call-lists">
            <summary className="cursor-pointer font-semibold text-slate-200">
              Video-call apps and sites ({v.custom_apps || v.custom_sites ? 'your lists' : 'the standard lists'})
            </summary>
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <label className="min-w-0 space-y-1">
                <span className="text-[11px] text-slate-300">Apps (one package name per line)</span>
                <textarea
                  value={appsText}
                  onChange={(e) => setApps(e.target.value)}
                  rows={8}
                  spellCheck={false}
                  className="w-full rounded-lg border border-white/10 bg-black/20 p-2 font-mono text-[11px] text-slate-100 focus:border-sky-300/40 focus:outline-none"
                  data-testid="video-call-apps"
                />
                <span className="block text-[10px] leading-relaxed text-slate-500">
                  {(v.apps || []).map((p) => v.labels[p]).filter(Boolean).join(', ')}
                </span>
              </label>
              <label className="min-w-0 space-y-1">
                <span className="text-[11px] text-slate-300">Sites (each covers the names under it)</span>
                <textarea
                  value={sitesText}
                  onChange={(e) => setSites(e.target.value)}
                  rows={8}
                  spellCheck={false}
                  className="w-full rounded-lg border border-white/10 bg-black/20 p-2 font-mono text-[11px] text-slate-100 focus:border-sky-300/40 focus:outline-none"
                  data-testid="video-call-sites"
                />
              </label>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                className={`${BUTTON} border-sky-300/40 bg-sky-400/15 text-sky-100 hover:border-sky-200/70`}
                disabled={busy || (apps === null && sites === null)}
                onClick={() =>
                  save.mutate(
                    {
                      ...(apps !== null ? { apps: parseList(apps) } : {}),
                      ...(sites !== null ? { sites: parseList(sites) } : {}),
                    },
                    { onSuccess: () => { setApps(null); setSites(null); } },
                  )
                }
                data-testid="video-call-lists-save"
              >
                Save lists
              </button>
              {(v.custom_apps || v.custom_sites) && (
                <button
                  type="button"
                  className={`${BUTTON} border-white/10 bg-white/5 text-slate-200 hover:border-sky-300/40`}
                  disabled={busy}
                  onClick={() => save.mutate({ apps: undefined, sites: undefined }, { onSuccess: () => { setApps(null); setSites(null); } })}
                  data-testid="video-call-lists-reset"
                >
                  Back to the standard lists
                </button>
              )}
            </div>
          </details>
        </div>
      )}
    </MemberCard>
  );
}

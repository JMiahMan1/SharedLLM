import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { disablePush, enablePush, permissionState, pushSupported } from '../../lib/push';
import type {
  TelemetryNotification,
  TelemetryReport,
  TelemetryReportPeriod,
  TelemetryReportType,
  TelemetrySchedule,
} from '../../types/api';

const PERIODS: Array<{ value: TelemetryReportPeriod; label: string }> = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Yearly' },
];

const REPORT_TYPES: Array<{ value: TelemetryReportType; label: string }> = [
  { value: 'health', label: 'Health & fitness' },
  { value: 'power', label: 'Power usage' },
];

const PRESET_TIMES = [
  { value: '12:00', label: 'Midday (12:00)' },
  { value: '21:00', label: 'End of day (21:00)' },
];

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/**
 * Scheduled + on-demand telemetry reports. Analysis only runs when the user
 * asks for it or when a schedule they enabled fires; the service defers runs
 * while Alpaca is busy and retries failures later.
 */
export default function TelemetryReportsPanel() {
  const [reportType, setReportType] = useState<TelemetryReportType>('health');
  const [period, setPeriod] = useState<TelemetryReportPeriod>('daily');
  const [runAt, setRunAt] = useState('21:00');
  const [enabled, setEnabled] = useState(true);
  const [jobs, setJobs] = useState<TelemetrySchedule[]>([]);
  const [reports, setReports] = useState<TelemetryReport[]>([]);
  const [notifications, setNotifications] = useState<TelemetryNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [pushState, setPushState] = useState<string>(() =>
    pushSupported() ? 'default' : 'unsupported'
  );

  const timezone = localTimezone();

  const loadData = useCallback(async () => {
    const [scheduleRes, reportRes, notificationRes] = await Promise.all([
      api.getTelemetrySchedules(),
      api.getTelemetryReports({ type: reportType, limit: 10 }),
      api.getTelemetryNotifications(10),
    ]);
    return {
      jobs: scheduleRes.jobs ?? [],
      reports: reportRes.reports ?? [],
      notifications: notificationRes.notifications ?? [],
    };
  }, [reportType]);

  const refresh = useCallback(async () => {
    try {
      const data = await loadData();
      setJobs(data.jobs);
      setReports(data.reports);
      setNotifications(data.notifications);
    } catch {
      // Scheduler not reachable yet — keep the panel usable
    } finally {
      setLoading(false);
    }
  }, [loadData]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await loadData();
        if (cancelled) return;
        setJobs(data.jobs);
        setReports(data.reports);
        setNotifications(data.notifications);
      } catch {
        // Scheduler not reachable yet — keep the panel usable
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [loadData]);

  useEffect(() => {
    if (!pushSupported()) return;
    navigator.serviceWorker
      .getRegistration('/push-sw.js')
      .then(async (registration) => {
        const subscription = await registration?.pushManager.getSubscription();
        setPushState(subscription ? 'granted' : permissionState());
      })
      .catch(() => setPushState(permissionState()));
  }, []);

  const togglePush = async () => {
    if (pushState === 'granted') {
      await disablePush();
      setPushState(permissionState());
      toast.success('Push notifications turned off');
      return;
    }
    const result = await enablePush();
    if (result.ok) {
      setPushState('granted');
      toast.success('Push notifications enabled');
    } else {
      setPushState(result.reason ?? 'denied');
      toast.error('Could not enable push notifications');
    }
  };

  const saveSchedule = async () => {
    try {
      await api.saveTelemetrySchedule({ type: reportType, period, run_at: runAt, timezone, enabled });
      toast.success(`${PERIODS.find((p) => p.value === period)?.label} ${reportType} report scheduled`);
      await refresh();
    } catch {
      toast.error('Could not save the report schedule');
    }
  };

  const requestNow = async () => {
    setRequesting(true);
    try {
      await api.requestTelemetryReport({ type: reportType, period, timezone });
      toast.success('Report queued — it runs when the local model is free');
      await refresh();
    } catch {
      toast.error('Could not queue the report');
    } finally {
      setRequesting(false);
    }
  };

  const removeSchedule = async (jobId: string) => {
    try {
      await api.deleteTelemetrySchedule(jobId);
      await refresh();
    } catch {
      toast.error('Could not remove the schedule');
    }
  };

  const activeJob = jobs.find((j) => j.type === reportType && j.period === period);

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-4" data-testid="telemetry-reports-panel">
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
          Health &amp; power reports
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          Reports run on its own model, never while the assistant is busy, and retry if they fail.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-slate-400 space-y-1">
          Report
          <select
            className="w-full glass-input px-2 py-1.5 rounded-lg text-sm text-slate-200"
            value={reportType}
            onChange={(e) => setReportType(e.target.value as TelemetryReportType)}
            aria-label="Report type"
          >
            {REPORT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400 space-y-1">
          Frequency
          <select
            className="w-full glass-input px-2 py-1.5 rounded-lg text-sm text-slate-200"
            value={period}
            onChange={(e) => setPeriod(e.target.value as TelemetryReportPeriod)}
            aria-label="Report frequency"
          >
            {PERIODS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {PRESET_TIMES.map((preset) => (
          <button
            key={preset.value}
            type="button"
            onClick={() => setRunAt(preset.value)}
            className={`px-3 py-1.5 text-xs rounded-lg border ${
              runAt === preset.value
                ? 'border-purple-400 text-purple-200'
                : 'border-white/10 text-slate-400'
            }`}
          >
            {preset.label}
          </button>
        ))}
        <input
          type="time"
          className="glass-input px-2 py-1.5 rounded-lg text-xs text-slate-200"
          value={runAt}
          onChange={(e) => setRunAt(e.target.value)}
          aria-label="Run time"
        />
        <span className="text-[11px] text-slate-500">{timezone}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="glass-button px-3 py-1.5 text-xs" onClick={saveSchedule}>
          {enabled ? 'Save schedule' : 'Save (paused)'}
        </button>
        <button
          type="button"
          className="glass-button px-3 py-1.5 text-xs"
          onClick={() => setEnabled((v) => !v)}
          aria-pressed={enabled}
        >
          {enabled ? 'Pause schedule' : 'Resume schedule'}
        </button>
        <button
          type="button"
          className="glass-button px-3 py-1.5 text-xs"
          onClick={requestNow}
          disabled={requesting}
        >
          {requesting ? 'Queuing…' : 'Request report now'}
        </button>
        {activeJob && (
          <button
            type="button"
            className="glass-button px-3 py-1.5 text-xs text-rose-300"
            onClick={() => removeSchedule(activeJob.id)}
          >
            Remove schedule
          </button>
        )}
        {pushState !== 'unsupported' && (
          <button
            type="button"
            className="glass-button px-3 py-1.5 text-xs"
            onClick={togglePush}
            data-testid="toggle-push"
          >
            {pushState === 'granted' ? 'Disable push alerts' : 'Enable push alerts'}
          </button>
        )}
      </div>

      {activeJob && (
        <p className="text-[11px] text-slate-500" data-testid="active-schedule">
          Next run: {activeJob.next_run_at ? new Date(activeJob.next_run_at).toLocaleString() : '—'}
          {activeJob.last_status ? ` · last: ${activeJob.last_status}` : ''}
        </p>
      )}

      {notifications.length > 0 && (
        <div className="space-y-1" data-testid="report-notifications">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Notifications</h3>
          {notifications.slice(-3).reverse().map((n) => (
            <p key={n.id} className="text-xs text-slate-400">
              {n.title}
            </p>
          ))}
        </div>
      )}

      <div className="space-y-2">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Recent reports</h3>
        {loading ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : reports.length === 0 ? (
          <p className="text-xs text-slate-500">No reports yet — request one whenever you want.</p>
        ) : (
          reports.map((report) => (
            <article
              key={report.id}
              className="rounded-xl border border-white/5 bg-white/[0.02] p-3"
              data-testid="report-item"
            >
              <div className="flex items-center justify-between text-[11px] text-slate-500">
                <span className="uppercase tracking-wider">
                  {report.period} · {report.type}
                </span>
                <span>{new Date(report.generated_at).toLocaleString()}</span>
              </div>
              <p className="text-xs text-slate-300 mt-1 whitespace-pre-wrap">
                {report.analysis ?? 'No data recorded for this period yet.'}
              </p>
            </article>
          ))
        )}
      </div>
    </div>
  );
}

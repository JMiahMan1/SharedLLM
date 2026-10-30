import React, { useState } from 'react';
import toast from 'react-hot-toast';
import {
  Battery,
  BatteryCharging,
  Bell,
  Bluetooth,
  BluetoothOff,
  Clock,
  Footprints,
  Gauge,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useWatchBle } from '../../hooks/useWatchBle';
import { syncWatchTime, type WatchStats } from '../../lib/watchBle';

/**
 * Connect the app to the Jarvis Watch directly over BLE.
 *
 * The watch is a plain Nordic UART peripheral: connect, read its snapshot, and
 * send it one-line commands. This panel is the manual version of that — the
 * web app normally reaches the watch through the gateway, so this is for the
 * phone in the same room (no router needed).
 */

const touch = 'min-h-11 pointer-coarse:min-h-11';

/** Seconds since boot as the largest two units that matter. */
function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '--';
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const mins = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

/** "deep" -> "Deep"; the firmware's power_mode options are single words. */
function titleCase(value: string): string {
  if (!value) return '--';
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function Stat({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl bg-white/5 px-3 py-2 min-w-0">
      <Icon size={18} className="text-sky-400 shrink-0" />
      <span className="min-w-0">
        <span className="block text-[10px] uppercase tracking-widest text-slate-500">{label}</span>
        <span className="block text-sm text-white truncate">{value}</span>
      </span>
    </div>
  );
}

function StatsGrid({ stats }: { stats: WatchStats }) {
  const BatteryIcon = stats.charging ? BatteryCharging : Battery;
  return (
    <div className="grid grid-cols-2 gap-2" data-testid="watch-stats">
      <Stat icon={BatteryIcon} label="Battery" value={stats.charging ? `Charging · ${Math.round(stats.battery)}%` : `${Math.round(stats.battery)}%`} />
      <Stat icon={Footprints} label="Steps" value={`${stats.steps.toLocaleString()} / ${stats.goal.toLocaleString()}`} />
      <Stat icon={Gauge} label="Power mode" value={titleCase(stats.mode)} />
      <Stat icon={Clock} label="Uptime" value={formatUptime(stats.uptime)} />
    </div>
  );
}

const WatchPanel: React.FC = () => {
  const { supported, connected, stats, connect, disconnect, refresh, sendNotification } = useWatchBle();
  const [busy, setBusy] = useState<'connect' | 'time' | 'notify' | null>(null);

  const onConnect = async () => {
    setBusy('connect');
    try {
      const ok = await connect();
      // A refused or timed-out connect is the common case (Bluetooth off, the
      // watch asleep); say so instead of claiming success.
      if (ok) toast.success('Watch connected');
      else toast.error('Could not reach the watch');
    } finally {
      setBusy(null);
    }
  };

  const onDisconnect = async () => {
    setBusy('connect');
    try {
      await disconnect();
      toast.success('Watch disconnected');
    } finally {
      setBusy(null);
    }
  };

  const onSyncTime = async () => {
    setBusy('time');
    try {
      await syncWatchTime();
      toast.success('Watch clock synced');
    } finally {
      setBusy(null);
    }
  };

  const onTestNotification = async () => {
    setBusy('notify');
    try {
      await sendNotification('Jarvis', 'Test notification from your phone.');
      toast.success('Notification sent');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="glass-panel overflow-hidden border-sky-500/10">
      <div className="p-4 sm:p-6 border-b border-white/5 flex items-center justify-between gap-3 bg-sky-500/5">
        <span className="text-[10px] font-black uppercase tracking-widest text-sky-400">Jarvis Watch</span>
        {supported && (
          <button
            onClick={connected ? onDisconnect : onConnect}
            disabled={busy === 'connect'}
            className={`glass-button text-xs px-4 ${connected ? 'bg-white/5 text-slate-300 border-white/10' : 'bg-sky-600/20 text-sky-300 border-sky-500/20'} ${touch}`}
          >
            {busy === 'connect' ? <Loader2 size={14} className="animate-spin" /> : connected ? <BluetoothOff size={14} /> : <Bluetooth size={14} />}
            {connected ? 'Disconnect' : 'Connect'}
          </button>
        )}
      </div>

      {!supported && (
        <p className="p-4 sm:p-6 text-sm text-slate-400">
          Bluetooth is not available here. Open Jarvis on the phone to talk to the watch directly.
        </p>
      )}

      {supported && (
        <div className="p-4 sm:p-6 space-y-4">
          <p className="flex items-center gap-2 text-xs text-slate-400">
            <span className={`inline-block w-2 h-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-slate-600'}`} aria-hidden />
            {connected ? 'Connected over Bluetooth' : 'Not connected'}
          </p>

          {connected && stats && <StatsGrid stats={stats} />}
          {connected && !stats && (
            <p className="text-sm text-slate-500">Waiting for the watch's snapshot…</p>
          )}

          {connected && (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => void refresh()}
                disabled={busy !== null}
                className={`glass-button px-4 text-sm ${touch}`}
              >
                <RefreshCw size={16} /> Refresh
              </button>
              <button
                onClick={onSyncTime}
                disabled={busy !== null}
                className={`glass-button px-4 text-sm ${touch}`}
              >
                {busy === 'time' ? <Loader2 size={16} className="animate-spin" /> : <Clock size={16} />} Sync time
              </button>
              <button
                onClick={onTestNotification}
                disabled={busy !== null}
                className={`glass-button px-4 text-sm ${touch}`}
              >
                {busy === 'notify' ? <Loader2 size={16} className="animate-spin" /> : <Bell size={16} />} Send test notification
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default WatchPanel;

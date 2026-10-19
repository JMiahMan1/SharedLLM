import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { HardDrive, Save } from 'lucide-react';
import toast from 'react-hot-toast';
import { formatBytes } from '../chat/chatModel';
import { errorText } from '../../lib/errorText';
import { api } from '../../services/api';
import type { StorageUsage } from '../../types/api';

const gib = (bytes: number) => Math.round((bytes / 1024 ** 3) * 100) / 100;

/** Per-user quota administration and volume totals for Artifacts. */
const SystemStoragePanel = () => {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const usage = useQuery({
    queryKey: ['system-storage-usage'],
    queryFn: () => api.getSystemStorageUsage(),
  });

  const quota = useMutation({
    mutationFn: ({ user, bytes }: { user: string; bytes: number }) => api.setStorageQuota(user, bytes),
    onSuccess: (updated: { username: string; quota_bytes: number }) => {
      toast.success(`Quota for ${updated.username} set to ${formatBytes(updated.quota_bytes) || '0 B'}`);
      queryClient.invalidateQueries({ queryKey: ['system-storage-usage'] });
      queryClient.invalidateQueries({ queryKey: ['artifact-usage'] });
    },
    onError: (error) => toast.error(errorText(error, 'Could not set the quota')),
  });

  const save = (row: StorageUsage) => {
    const value = drafts[row.user] ?? String(gib(row.quota));
    const gb = Number(value);
    if (!Number.isFinite(gb) || gb < 0) {
      toast.error('Enter a non-negative number of GB');
      return;
    }
    quota.mutate({ user: row.user, bytes: Math.round(gb * 1024 ** 3) });
  };

  if (usage.isLoading) return <p className="text-sm text-slate-400">Reading storage usage…</p>;
  if (usage.isError) return <p className="text-sm text-rose-300">{errorText(usage.error, 'Storage usage is unavailable')}</p>;
  if (!usage.data) return null;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="glass-card p-4">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">Artifacts used</p>
          <p className="text-xl font-black text-white mt-1">{formatBytes(usage.data.used) || '0 B'}</p>
        </div>
        <div className="glass-card p-4">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">Allocated quota</p>
          <p className="text-xl font-black text-white mt-1">{formatBytes(usage.data.quota) || '0 B'}</p>
        </div>
        <div className="glass-card p-4">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">Free on volume</p>
          <p className="text-xl font-black text-white mt-1">{formatBytes(usage.data.free) || '0 B'}</p>
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        <div className="px-4 py-3 border-b border-white/5 flex items-center gap-2">
          <HardDrive size={16} className="text-purple-300" />
          <h2 className="text-sm font-bold text-white">Per-user storage</h2>
        </div>
        {usage.data.users.length === 0 ? (
          <p className="p-4 text-sm text-slate-400">No one has stored an artifact yet.</p>
        ) : (
          <div className="divide-y divide-white/5">
            {usage.data.users.map((row) => {
              const pct = row.quota > 0 ? Math.min(100, (row.used / row.quota) * 100) : 0;
              return (
                <div key={row.user} className="p-4 flex flex-wrap items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm font-semibold text-white">{row.user}</p>
                      <p className="text-xs text-slate-400">
                        {formatBytes(row.used) || '0 B'} of {formatBytes(row.quota) || '0 B'} · {row.count} file{row.count === 1 ? '' : 's'}
                      </p>
                    </div>
                    <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden mt-2">
                      <div className={`h-full rounded-full ${row.warn ? 'bg-amber-500' : 'bg-purple-500'}`} style={{ width: `${Math.max(pct > 0 ? 2 : 0, pct)}%` }} />
                    </div>
                  </div>
                  <label className="flex items-center gap-1.5 text-xs text-slate-400">
                    Quota
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={drafts[row.user] ?? String(gib(row.quota))}
                      onChange={(e) => setDrafts((current) => ({ ...current, [row.user]: e.target.value }))}
                      aria-label={`Quota in GB for ${row.user}`}
                      className="w-20 glass-card px-2 py-1 text-sm text-white bg-transparent"
                    />
                    GB
                  </label>
                  <button
                    onClick={() => save(row)}
                    aria-label={`Save quota for ${row.user}`}
                    className="px-2.5 py-1.5 rounded-lg text-xs bg-purple-600/80 hover:bg-purple-600 text-white flex items-center gap-1.5"
                  >
                    <Save size={13} /> Save
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default SystemStoragePanel;

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Globe, Plus, Trash2, Save } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';

interface DnsMapping {
  hostname: string;
  ip: string;
}

export default function DnsManagementPanel() {
  const queryClient = useQueryClient();
  const [newHost, setNewHost] = useState('');
  const [newIp, setNewIp] = useState('');
  const [upstream, setUpstream] = useState('');
  const [pollInterval, setPollInterval] = useState(30);

  const { data: dnsConfig, isLoading } = useQuery({
    queryKey: ['dns-config'],
    queryFn: () => api.getDnsConfig(),
    refetchInterval: 10000,
  });

  const mappings: DnsMapping[] = dnsConfig
    ? Object.entries(dnsConfig.dns_mappings || {}).map(([hostname, ip]) => ({ hostname, ip }))
    : [];

  const updateMutation = useMutation({
    mutationFn: (config: { dns_mappings?: Record<string, string>; dns_upstream?: string; dns_poll_interval?: number }) =>
      api.updateDnsConfig(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dns-config'] });
      toast.success('DNS configuration updated');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to update DNS config'),
  });

  const registerMutation = useMutation({
    mutationFn: ({ hostname, ip }: { hostname: string; ip: string }) =>
      api.registerDnsEntry(hostname, ip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dns-config'] });
      setNewHost('');
      setNewIp('');
      toast.success('DNS entry registered');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to register DNS entry'),
  });

  const removeMutation = useMutation({
    mutationFn: (hostname: string) => api.removeDnsEntry(hostname),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dns-config'] });
      toast.success('DNS entry removed');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to remove DNS entry'),
  });

  if (isLoading) {
    return <div className="text-slate-400 animate-pulse text-sm">Loading DNS configuration...</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="flex items-center gap-3 text-xl font-bold text-white">
          <Globe size={20} className="text-blue-400" />
          DNS Network Resolution
        </h3>
        <p className="mt-1 text-sm text-slate-400">Manage internal hostname-to-IP mappings. Changes propagate automatically to all containers.</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="glass-card p-4 border border-white/10">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">Upstream DNS</p>
          <p className="text-sm text-slate-300 mb-3">Fallback DNS servers for external resolution.</p>
          <div className="flex gap-2">
            <input
              value={dnsConfig?.dns_upstream || ''}
              onChange={(e) => setUpstream(e.target.value)}
              className="glass-input flex-1 text-xs"
              placeholder="8.8.8.8,1.1.1.1"
            />
            <button
              onClick={() => updateMutation.mutate({ dns_upstream: upstream || dnsConfig?.dns_upstream })}
              disabled={updateMutation.isPending}
              className="glass-button px-3 py-1 text-[10px] font-black uppercase"
            >
              <Save size={12} />
            </button>
          </div>
        </div>

        <div className="glass-card p-4 border border-white/10">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">Sync Poll Interval</p>
          <p className="text-sm text-slate-300 mb-3">Seconds between DNS sync polls.</p>
          <div className="flex gap-2">
            <input
              type="number"
              min={5}
              max={300}
              value={pollInterval}
              onChange={(e) => setPollInterval(parseInt(e.target.value) || 30)}
              className="glass-input flex-1 text-xs"
            />
            <button
              onClick={() => updateMutation.mutate({ dns_poll_interval: pollInterval })}
              disabled={updateMutation.isPending}
              className="glass-button px-3 py-1 text-[10px] font-black uppercase"
            >
              <Save size={12} />
            </button>
          </div>
        </div>
      </div>

      <div className="glass-card p-4 border border-white/10">
        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-3">Add DNS Entry</p>
        <div className="flex gap-2">
          <input
            value={newHost}
            onChange={(e) => setNewHost(e.target.value)}
            className="glass-input flex-1 text-xs"
            placeholder="hostname (e.g., ollama-server)"
          />
          <input
            value={newIp}
            onChange={(e) => setNewIp(e.target.value)}
            className="glass-input flex-1 text-xs"
            placeholder="IP address (e.g., 192.168.2.114)"
          />
          <button
            onClick={() => {
              if (newHost && newIp) {
                registerMutation.mutate({ hostname: newHost, ip: newIp });
              }
            }}
            disabled={registerMutation.isPending || !newHost || !newIp}
            className="glass-button px-3 py-1 text-[10px] font-black uppercase"
          >
            <Plus size={12} /> Add
          </button>
        </div>
      </div>

      <div>
        <h4 className="flex items-center gap-2 text-sm font-bold text-slate-300 mb-4 uppercase tracking-widest">
          <Globe size={16} className="text-blue-400" />
          Active Mappings ({mappings.length})
        </h4>

        {mappings.length === 0 ? (
          <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
            No DNS mappings configured. Add entries above.
          </div>
        ) : (
          <div className="space-y-2">
            {mappings.map((m) => (
              <div key={m.hostname} className="glass-card p-3 border border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-mono text-white">{m.hostname}</span>
                  <span className="text-slate-600">→</span>
                  <span className="text-sm font-mono text-blue-300">{m.ip}</span>
                </div>
                <button
                  onClick={() => removeMutation.mutate(m.hostname)}
                  className="glass-button px-2 py-1 text-red-400 hover:text-red-300"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

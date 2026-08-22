import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Cpu, Plus, Trash2, Save, Radio, Zap, Link2 } from 'lucide-react';
import { api, type EsphomeDevice } from '../../services/api';

const HardwarePanel: React.FC = () => {
  const queryClient = useQueryClient();
  const [newDevice, setNewDevice] = useState<Partial<EsphomeDevice>>({});
  const [testing, setTesting] = useState<string | null>(null);

  const { data: devices = [], isLoading } = useQuery({
    queryKey: ['esphome-devices'],
    queryFn: () => api.getEsphomeDevices(),
    retry: 1,
  });

  const saveMutation = useMutation({
    mutationFn: (list: EsphomeDevice[]) => api.saveEsphomeDevices(list),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['esphome-devices'] });
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setNewDevice({});
      toast.success('Hardware registry saved');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to save devices'),
  });

  const testMutation = useMutation({
    mutationFn: (device: string) => {
      setTesting(device);
      return api.esphomeList(device);
    },
    onSuccess: (result) => {
      if (result.status === 'SUCCESS') {
        const detail = result.detail as { entities?: unknown[] } | undefined;
        toast.success(`${device}: ${detail?.entities?.length ?? 0} entities reachable`);
      } else {
        toast.error(result.message || `${device}: connection failed`);
      }
    },
    onError: (err: Error) => toast.error(err.message || 'Connection test failed'),
    onSettled: () => setTesting(null),
  });

  const handleAdd = () => {
    if (!newDevice.name?.trim() || !newDevice.host?.trim()) {
      toast.error('Name and host are required');
      return;
    }
    if (devices.some(d => d.name === newDevice.name!.trim())) {
      toast.error(`Device '${newDevice.name.trim()}' already exists`);
      return;
    }
    const device: EsphomeDevice = {
      name: newDevice.name.trim(),
      host: newDevice.host.trim(),
      ...(newDevice.port ? { port: Number(newDevice.port) } : {}),
      ...(newDevice.noise_psk?.trim() ? { noise_psk: newDevice.noise_psk.trim() } : {}),
      ...(newDevice.ha_entity_id?.trim() ? { ha_entity_id: newDevice.ha_entity_id.trim().toLowerCase() } : {}),
    };
    saveMutation.mutate([...devices, device]);
  };

  const handleRemove = (name: string) => {
    saveMutation.mutate(devices.filter(d => d.name !== name));
  };

  const handleFieldChange = (name: string, field: keyof EsphomeDevice, value: string) => {
    saveMutation.mutate(
      devices.map(d => {
        if (d.name !== name) return d;
        if (field === 'ha_entity_id') {
          return { ...d, ha_entity_id: value.trim().toLowerCase() || undefined };
        }
        return { ...d, [field]: value };
      }),
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-12 text-slate-500 animate-pulse">
        <Zap className="mr-2 animate-bounce" size={20} />
        Loading hardware registry...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-xs text-slate-400">
        ESPHome devices are controlled directly over their native API when Home Assistant is
        unreachable. Devices with an HA entity mapping are routed through HA first and fall back
        to the direct path only on connection failure.
      </p>

      {/* Device list */}
      <div className="space-y-3 max-h-96 overflow-y-auto pr-1">
        {devices.length === 0 && (
          <div className="text-center py-8 text-xs text-slate-500 border border-dashed border-white/10 rounded-xl">
            No ESPHome devices configured yet.
          </div>
        )}
        {devices.map(device => (
          <div key={device.name} className="glass-card p-4 bg-white/5 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <Cpu size={16} className={testing === device.name ? 'text-emerald-400 animate-pulse' : 'text-slate-500'} />
                <span className="font-bold text-sm text-white truncate">{device.name}</span>
                <span
                  className={`px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-widest ${
                    device.ha_entity_id
                      ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                      : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  }`}
                  title={device.ha_entity_id
                    ? 'Mapped to Home Assistant; direct ESPHome used as fallback'
                    : 'Direct ESPHome native API only'}
                >
                  {device.ha_entity_id ? 'Both' : 'Direct'}
                </span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => testMutation.mutate(device.name)}
                  disabled={testMutation.isPending}
                  className="glass-button px-3 py-1.5 text-[10px] font-black uppercase tracking-widest flex items-center gap-1"
                  title="Connect to the device and list its entities"
                >
                  <Radio size={12} />
                  {testing === device.name ? 'Testing...' : 'Test'}
                </button>
                <button
                  onClick={() => handleRemove(device.name)}
                  disabled={saveMutation.isPending}
                  className="glass-button px-3 py-1.5 text-[10px] font-black uppercase tracking-widest text-red-300 hover:text-red-200 flex items-center gap-1"
                  title={`Remove ${device.name}`}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <input
                type="text"
                value={device.host}
                onChange={e => handleFieldChange(device.name, 'host', e.target.value)}
                className="glass-input w-full text-xs"
                placeholder="Host / IP"
                aria-label={`${device.name} host`}
              />
              <input
                type="number"
                value={device.port ?? 6053}
                onChange={e => handleFieldChange(device.name, 'port', e.target.value)}
                className="glass-input w-full text-xs"
                placeholder="Port"
                aria-label={`${device.name} port`}
              />
              <input
                type="password"
                value={device.noise_psk ?? ''}
                onChange={e => handleFieldChange(device.name, 'noise_psk', e.target.value)}
                className="glass-input w-full text-xs"
                placeholder="Noise PSK (optional)"
                aria-label={`${device.name} noise psk`}
              />
              <input
                type="text"
                value={device.ha_entity_id ?? ''}
                onChange={e => handleFieldChange(device.name, 'ha_entity_id', e.target.value)}
                className="glass-input w-full text-xs"
                placeholder="HA entity (e.g. light.office)"
                aria-label={`${device.name} home assistant mapping`}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Add device */}
      <div className="glass-panel p-4 border-white/10 space-y-3">
        <h5 className="text-[10px] font-black uppercase tracking-widest text-slate-400 flex items-center gap-2">
          <Link2 size={14} />
          Add Device
        </h5>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <input
            type="text"
            value={newDevice.name ?? ''}
            onChange={e => setNewDevice({ ...newDevice, name: e.target.value })}
            className="glass-input w-full text-xs"
            placeholder="Name *"
            aria-label="New device name"
          />
          <input
            type="text"
            value={newDevice.host ?? ''}
            onChange={e => setNewDevice({ ...newDevice, host: e.target.value })}
            className="glass-input w-full text-xs"
            placeholder="Host / IP *"
            aria-label="New device host"
          />
          <input
            type="number"
            value={newDevice.port ?? ''}
            onChange={e => setNewDevice({ ...newDevice, port: e.target.value ? Number(e.target.value) : undefined })}
            className="glass-input w-full text-xs"
            placeholder="Port"
            aria-label="New device port"
          />
          <input
            type="password"
            value={newDevice.noise_psk ?? ''}
            onChange={e => setNewDevice({ ...newDevice, noise_psk: e.target.value })}
            className="glass-input w-full text-xs"
            placeholder="Noise PSK"
            aria-label="New device noise psk"
          />
          <input
            type="text"
            value={newDevice.ha_entity_id ?? ''}
            onChange={e => setNewDevice({ ...newDevice, ha_entity_id: e.target.value })}
            className="glass-input w-full text-xs"
            placeholder="HA entity mapping"
            aria-label="New device home assistant mapping"
          />
        </div>
        <button
          onClick={handleAdd}
          disabled={saveMutation.isPending}
          className="glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest flex items-center gap-2"
        >
          <Plus size={14} />
          Add Device
        </button>
      </div>

      {saveMutation.isPending && (
        <div className="flex items-center justify-end gap-2 text-[10px] uppercase tracking-widest text-slate-500">
          <Save size={12} className="animate-spin" />
          Saving registry...
        </div>
      )}
    </div>
  );
};

export default HardwarePanel;

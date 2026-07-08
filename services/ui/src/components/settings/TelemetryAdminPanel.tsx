import { useState, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  TrendingUp,
  Plus,
  Trash2,
  Edit3,
  Save,
  Eye,
  BarChart3,
  Zap,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Power,
  Radio,
  Monitor,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { TelemetryEnrollment } from '../../services/api';
import type { TelemetryDataPoint, TelemetrySummary, TelemetryInsights } from '../../types/api';
import EntitySearchDropdown from '../ui/EntitySearchDropdown';

const TelemetryAdminPanel = () => {
  const queryClient = useQueryClient();
  const [activeEntity, setActiveEntity] = useState<string | null>(null);

  const { data: telemetryEnrollments = [] } = useQuery<TelemetryEnrollment[]>({
    queryKey: ['telemetry-enrollments'],
    queryFn: () => api.getTelemetryEnrollments(),
  });

  const { data: enrolledEntityData } = useQuery<TelemetryDataPoint[]>({
    queryKey: ['telemetry-data', activeEntity],
    queryFn: () => activeEntity ? api.getTelemetryData(activeEntity, 24) : null,
    enabled: !!activeEntity,
  });

  const { data: enrolledEntitySummary } = useQuery<TelemetrySummary>({
    queryKey: ['telemetry-summary', activeEntity],
    queryFn: () => activeEntity ? api.getTelemetrySummary(activeEntity) : null,
    enabled: !!activeEntity,
  });

  const { data: enrolledEntityInsights } = useQuery<TelemetryInsights>({
    queryKey: ['telemetry-insights', activeEntity],
    queryFn: () => activeEntity ? api.getTelemetryInsights(activeEntity) : null,
    enabled: !!activeEntity,
  });

  const enrollTelemetryMutation = useMutation({
    mutationFn: (data: Partial<TelemetryEnrollment>) => api.enrollTelemetry(data.entity_id!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telemetry-enrollments'] });
      setTelemetryEntityId('');
      setTelemetryPowerTracking(true);
      setTelemetryAvailabilityTracking(true);
      setTelemetryUsageTracking(true);
      setTelemetryOfflineThreshold(30);
      setTelemetryGroupId('');
      toast.success('Device enrolled in telemetry');
    },
    onError: () => toast.error('Failed to enroll device'),
  });

  const updateTelemetryMutation = useMutation({
    mutationFn: ({ entityId, updates }: { entityId: string; updates: Partial<TelemetryEnrollment> }) =>
      api.enrollTelemetry(entityId, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telemetry-enrollments'] });
      toast.success('Telemetry settings updated');
    },
    onError: () => toast.error('Failed to update telemetry settings'),
  });

  const unenrollTelemetryMutation = useMutation({
    mutationFn: (entity_id: string) => api.unenrollTelemetry(entity_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telemetry-enrollments'] });
      setActiveEntity(null);
      toast.success('Device unenrolled from telemetry');
    },
    onError: () => toast.error('Failed to unenroll device'),
  });

  const analyzeTelemetryMutation = useMutation({
    mutationFn: () => api.analyzeTelemetry(),
    onSuccess: () => {
      toast.success('Telemetry analysis queued');
    },
    onError: () => toast.error('Failed to queue analysis'),
  });

  const snapshotTelemetryMutation = useMutation({
    mutationFn: (entityId: string) => api.triggerTelemetrySnapshot(entityId),
    onSuccess: (_, entityId) => {
      toast.success(`Manual snapshot triggered for ${entityId}`);
    },
    onError: () => toast.error('Failed to trigger snapshot'),
  });

  // Local form state
  const [telemetryEntityId, setTelemetryEntityId] = useState('');
  const [telemetryPowerTracking, setTelemetryPowerTracking] = useState(true);
  const [telemetryAvailabilityTracking, setTelemetryAvailabilityTracking] = useState(true);
  const [telemetryUsageTracking, setTelemetryUsageTracking] = useState(true);
  const [telemetryOfflineThreshold, setTelemetryOfflineThreshold] = useState(30);
  const [telemetryGroupId, setTelemetryGroupId] = useState('');

  // Edit mode state
  const [editingEntity, setEditingEntity] = useState<string | null>(null);
  const [editPowerTracking, setEditPowerTracking] = useState(true);
  const [editAvailabilityTracking, setEditAvailabilityTracking] = useState(true);
  const [editUsageTracking, setEditUsageTracking] = useState(true);
  const [editOfflineThreshold, setEditOfflineThreshold] = useState(30);

  const handleEnroll = () => {
    if (!telemetryEntityId.trim()) {
      toast.error('Enter an entity ID');
      return;
    }
    enrollTelemetryMutation.mutate({
      entity_id: telemetryEntityId.trim(),
      power_tracking: telemetryPowerTracking,
      availability_tracking: telemetryAvailabilityTracking,
      usage_tracking: telemetryUsageTracking,
      offline_alert_threshold_minutes: telemetryOfflineThreshold,
      group_id: telemetryGroupId || undefined,
    });
  };

  const startEdit = (enrollment: TelemetryEnrollment) => {
    setEditingEntity(enrollment.entity_id);
    setEditPowerTracking(enrollment.power_tracking);
    setEditAvailabilityTracking(enrollment.availability_tracking);
    setEditUsageTracking(enrollment.usage_tracking);
    setEditOfflineThreshold(enrollment.offline_alert_threshold_minutes);
  };

  const cancelEdit = () => {
    setEditingEntity(null);
  };

  const saveEdit = (entityId: string) => {
    updateTelemetryMutation.mutate({
      entityId,
      updates: {
        entity_id: entityId,
        power_tracking: editPowerTracking,
        availability_tracking: editAvailabilityTracking,
        usage_tracking: editUsageTracking,
        offline_alert_threshold_minutes: editOfflineThreshold,
      },
    });
    setEditingEntity(null);
  };

  const stats = useMemo(() => {
    const totalDevices = telemetryEnrollments.length;
    const powerTracking = telemetryEnrollments.filter(e => e.power_tracking).length;
    const availabilityTracking = telemetryEnrollments.filter(e => e.availability_tracking).length;
    const usageTracking = telemetryEnrollments.filter(e => e.usage_tracking).length;
    const enrolledGroups = new Set(telemetryEnrollments.map(e => e.entity_id.split('.')[0])).size;
    return { totalDevices, powerTracking, availabilityTracking, usageTracking, enrolledGroups };
  }, [telemetryEnrollments]);

  const formatTimestamp = (ts: number) => {
    return new Date(ts * 1000).toLocaleString();
  };

  const formatPower = (w: number | null) => {
    if (w == null) return '--';
    return `${w.toFixed(1)} W`;
  };

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <section className="glass-panel p-6">
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-cyan-500/10">
            <Activity size={16} className="text-cyan-400" />
          </div>
          <div>
            <h3 className="text-base font-bold text-white">Telemetry Overview</h3>
            <p className="text-[10px] text-slate-500">System-wide telemetry monitoring status</p>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="glass-card p-3 text-center">
            <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Total Devices</p>
            <p className="text-lg font-bold text-white mt-1">{stats.totalDevices}</p>
          </div>
          <div className="glass-card p-3 text-center">
            <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Power Tracking</p>
            <div className="flex items-center justify-center gap-1 mt-1">
              <Power size={12} className="text-amber-400" />
              <p className="text-lg font-bold text-amber-400">{stats.powerTracking}</p>
            </div>
          </div>
          <div className="glass-card p-3 text-center">
            <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Availability</p>
            <div className="flex items-center justify-center gap-1 mt-1">
              <Radio size={12} className="text-emerald-400" />
              <p className="text-lg font-bold text-emerald-400">{stats.availabilityTracking}</p>
            </div>
          </div>
          <div className="glass-card p-3 text-center">
            <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Usage Tracking</p>
            <div className="flex items-center justify-center gap-1 mt-1">
              <Monitor size={12} className="text-blue-400" />
              <p className="text-lg font-bold text-blue-400">{stats.usageTracking}</p>
            </div>
          </div>
          <div className="glass-card p-3 text-center">
            <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Data Points</p>
            <p className="text-lg font-bold text-slate-400 mt-1">~{stats.totalDevices * 1440}</p>
            <p className="text-[8px] text-slate-600 mt-0.5">per 24h (est.)</p>
          </div>
        </div>
      </section>

      {/* Enrollment Form */}
      <section className="glass-panel p-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h3 className="flex items-center gap-3 text-xl font-bold text-white">
              <Plus size={20} className="text-emerald-400" />
              Enroll Device
            </h3>
            <p className="mt-1 text-sm text-slate-400">
              Add a Home Assistant entity for telemetry monitoring.
            </p>
          </div>
          <button
            onClick={() => analyzeTelemetryMutation.mutate()}
            disabled={analyzeTelemetryMutation.isPending}
            className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
          >
            <TrendingUp size={14} />
            Run LLM Analysis
          </button>
        </div>

        <div className="grid gap-4 md:grid-cols-[2fr_1fr_1fr] lg:grid-cols-[2fr_1fr_1fr_auto]">
          <div className="md:col-span-2">
            <EntitySearchDropdown
              value={telemetryEntityId}
              onChange={setTelemetryEntityId}
              placeholder="Search HA entities for telemetry..."
              domainFilter="sensor"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 mb-1.5">Offline Alert Threshold</label>
            <input
              type="number"
              value={telemetryOfflineThreshold}
              onChange={(e) => setTelemetryOfflineThreshold(Number(e.target.value))}
              className="glass-input w-full"
              min={1}
              max={1440}
            />
            <p className="text-[10px] text-slate-600 mt-1">Minutes of silence before alert</p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 mb-1.5">Group ID</label>
            <input
              type="text"
              value={telemetryGroupId}
              onChange={(e) => setTelemetryGroupId(e.target.value)}
              placeholder="Optional grouping"
              className="glass-input w-full"
            />
            <p className="text-[10px] text-slate-600 mt-1">Group related devices</p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4">
          <label className="flex items-center gap-3 glass-card p-3 cursor-pointer hover:border-white/10 transition-colors">
            <input
              type="checkbox"
              checked={telemetryPowerTracking}
              onChange={(e) => setTelemetryPowerTracking(e.target.checked)}
              className="rounded"
            />
            <div>
              <p className="text-sm font-medium text-white">Power Tracking</p>
              <p className="text-[10px] text-slate-500">Track power consumption in watts</p>
            </div>
            <Zap size={14} className="text-amber-400 ml-auto shrink-0" />
          </label>

          <label className="flex items-center gap-3 glass-card p-3 cursor-pointer hover:border-white/10 transition-colors">
            <input
              type="checkbox"
              checked={telemetryAvailabilityTracking}
              onChange={(e) => setTelemetryAvailabilityTracking(e.target.checked)}
              className="rounded"
            />
            <div>
              <p className="text-sm font-medium text-white">Availability Tracking</p>
              <p className="text-[10px] text-slate-500">Monitor online/offline status</p>
            </div>
            <Radio size={14} className="text-emerald-400 ml-auto shrink-0" />
          </label>

          <label className="flex items-center gap-3 glass-card p-3 cursor-pointer hover:border-white/10 transition-colors">
            <input
              type="checkbox"
              checked={telemetryUsageTracking}
              onChange={(e) => setTelemetryUsageTracking(e.target.checked)}
              className="rounded"
            />
            <div>
              <p className="text-sm font-medium text-white">Usage Tracking</p>
              <p className="text-[10px] text-slate-500">Track on/off state transitions</p>
            </div>
            <Monitor size={14} className="text-blue-400 ml-auto shrink-0" />
          </label>
        </div>

        <div className="flex justify-end mt-4">
          <button
            onClick={handleEnroll}
            disabled={enrollTelemetryMutation.isPending || !telemetryEntityId.trim()}
            className="glass-button px-6 py-3 text-[10px] font-black uppercase tracking-widest"
          >
            <Plus size={14} />
            Enroll Device
          </button>
        </div>
      </section>

      {/* Enrolled Devices List */}
      <section className="glass-panel p-6">
        <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
          <BarChart3 size={20} className="text-emerald-400" />
          Enrolled Devices
        </h3>

        {telemetryEnrollments.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Activity size={32} className="text-slate-700 mb-3" />
            <p className="text-sm text-slate-500">No devices enrolled in telemetry monitoring.</p>
            <p className="text-xs text-slate-600 mt-1">Use the form above to enroll a device.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {telemetryEnrollments.map((enrollment) => (
              <div
                key={enrollment.entity_id}
                className="glass-card p-4 space-y-3"
              >
                {/* Header */}
                <div className="flex items-center justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="font-semibold text-white truncate">{enrollment.entity_id}</h4>
                      <span className="text-[9px] font-bold uppercase tracking-widest text-slate-600 shrink-0">
                        {enrollment.entity_id.split('.')[0]}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 mt-1 text-xs text-slate-400">
                      <span className="flex items-center gap-1">
                        <Power size={11} className={enrollment.power_tracking ? 'text-amber-400' : 'text-slate-600'} />
                        Power: {enrollment.power_tracking ? 'On' : 'Off'}
                      </span>
                      <span className="flex items-center gap-1">
                        <Radio size={11} className={enrollment.availability_tracking ? 'text-emerald-400' : 'text-slate-600'} />
                        Availability: {enrollment.availability_tracking ? 'On' : 'Off'}
                      </span>
                      <span className="flex items-center gap-1">
                        <Monitor size={11} className="text-blue-400" />
                        Usage: On
                      </span>
                      <span className="flex items-center gap-1">
                        <AlertTriangle size={11} className="text-amber-500" />
                        Alert: {enrollment.offline_alert_threshold_minutes}min
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => setActiveEntity(enrollment.entity_id)}
                      className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
                      title="View data"
                      aria-label={`View data for ${enrollment.entity_id}`}
                    >
                      <Eye size={14} />
                    </button>
                    <button
                      onClick={() => snapshotTelemetryMutation.mutate(enrollment.entity_id)}
                      disabled={snapshotTelemetryMutation.isPending}
                      className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
                      title="Trigger snapshot"
                      aria-label={`Trigger snapshot for ${enrollment.entity_id}`}
                    >
                      <RefreshCw size={14} />
                    </button>
                    <button
                      onClick={() => startEdit(enrollment)}
                      className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
                      title="Edit"
                      aria-label={`Edit telemetry settings for ${enrollment.entity_id}`}
                    >
                      <Edit3 size={14} />
                    </button>
                    <button
                      onClick={() => unenrollTelemetryMutation.mutate(enrollment.entity_id)}
                      className="p-2 rounded-lg text-slate-400 hover:text-red-300 hover:bg-red-500/10 transition-colors"
                      title="Unenroll"
                      aria-label={`Unenroll ${enrollment.entity_id}`}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* Expanded Data View */}
                {activeEntity === enrollment.entity_id && (
                  <div className="border-t border-white/5 pt-4 space-y-4">
                    {/* Summary */}
                    {enrolledEntitySummary?.summary && (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <div className="glass-card p-3 text-center">
                          <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Current</p>
                          <p className="text-base font-bold text-amber-400">{formatPower(enrolledEntitySummary.summary.current_power_w)}</p>
                        </div>
                        <div className="glass-card p-3 text-center">
                          <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Peak</p>
                          <p className="text-base font-bold text-red-400">{formatPower(enrolledEntitySummary.summary.peak_power_w)}</p>
                        </div>
                        <div className="glass-card p-3 text-center">
                          <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Average</p>
                          <p className="text-base font-bold text-blue-400">{formatPower(enrolledEntitySummary.summary.avg_power_w)}</p>
                        </div>
                        <div className="glass-card p-3 text-center">
                          <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Availability</p>
                          <p className="text-base font-bold text-emerald-400">{enrolledEntitySummary.summary.availability_pct?.toFixed(1)}%</p>
                        </div>
                      </div>
                    )}

                    {/* Recent Data Points */}
                    {enrolledEntityData && enrolledEntityData.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-slate-400 mb-2">Recent Data Points (last 12)</h5>
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                          {enrolledEntityData.slice(0, 12).map((dp, idx) => (
                            <div key={idx} className="glass-card p-2 text-xs">
                              <div className="flex items-center justify-between text-[10px] text-slate-500 mb-1">
                                <span>{formatTimestamp(dp.recorded_at)}</span>
                                {dp.source && <span className="text-slate-600">{dp.source}</span>}
                              </div>
                              {dp.power_w != null && (
                                <p className="font-medium text-amber-400">{dp.power_w.toFixed(1)} W</p>
                              )}
                              {dp.state && (
                                <p className="text-slate-400">State: {dp.state}</p>
                              )}
                              {dp.is_available !== undefined && (
                                <p className={`text-slate-400`}>
                                  {dp.is_available ? <><CheckCircle2 size={11} className="inline text-emerald-400" /> Available</> : <><AlertTriangle size={11} className="inline text-red-400" /> Offline</>}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Insights */}
                    {enrolledEntityInsights && enrolledEntityInsights.insights && enrolledEntityInsights.insights.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-slate-400 mb-2">LLM Insights</h5>
                        <div className="space-y-2">
                          {enrolledEntityInsights.insights.map((insight, idx) => (
                            <div key={idx} className="glass-card p-3">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-[9px] font-bold uppercase tracking-widest text-indigo-400">
                                  {insight.type}
                                </span>
                                <span className="text-[9px] text-slate-600">
                                  {new Date(insight.timestamp * 1000).toLocaleString()}
                                </span>
                              </div>
                              <p className="text-xs text-slate-300">{insight.message}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Edit Mode */}
                {editingEntity === enrollment.entity_id && (
                  <div className="border-t border-white/5 pt-4 space-y-3">
                    <h5 className="text-sm font-semibold text-white">Edit Telemetry Settings</h5>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                      <label className="flex items-center gap-3 glass-card p-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={editPowerTracking}
                          onChange={(e) => setEditPowerTracking(e.target.checked)}
                          className="rounded"
                        />
                        <span className="text-sm text-white">Power Tracking</span>
                      </label>
                      <label className="flex items-center gap-3 glass-card p-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={editAvailabilityTracking}
                          onChange={(e) => setEditAvailabilityTracking(e.target.checked)}
                          className="rounded"
                        />
                        <span className="text-sm text-white">Availability Tracking</span>
                      </label>
                      <div>
                        <label className="block text-xs font-semibold text-slate-400 mb-1.5">Offline Alert (min)</label>
                        <input
                          type="number"
                          value={editOfflineThreshold}
                          onChange={(e) => setEditOfflineThreshold(Number(e.target.value))}
                          className="glass-input w-full"
                          min={1}
                          max={1440}
                        />
                      </div>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={cancelEdit}
                        className="glass-button px-4 py-2 text-xs"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => saveEdit(enrollment.entity_id)}
                        disabled={updateTelemetryMutation.isPending}
                        className="glass-button px-4 py-2 text-xs"
                      >
                        <Save size={12} className="mr-1" />
                        Save
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

export default TelemetryAdminPanel;

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Globe, Plus, Trash2, Save, Edit2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';

interface DnsRecord {
  id: number;
  domain: string;
  record_type: string;
  values: string[];
  ttl: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface DnsRecordForm {
  domain: string;
  record_type: 'A' | 'CNAME';
  values: string[];
  ttl: number;
}

const emptyForm: DnsRecordForm = {
  domain: '',
  record_type: 'A',
  values: [''],
  ttl: 300,
};

export default function DnsManagementPanel() {
  const queryClient = useQueryClient();
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingRecord, setEditingRecord] = useState<DnsRecord | null>(null);
  const [form, setForm] = useState<DnsRecordForm>(() => {
    if (editingRecord) {
      return {
        domain: editingRecord.domain,
        record_type: editingRecord.record_type as 'A' | 'CNAME',
        values: [...editingRecord.values],
        ttl: editingRecord.ttl,
      };
    }
    return emptyForm;
  });

  const { data: records = [], isLoading } = useQuery({
    queryKey: ['dns-records'],
    queryFn: () => api.getDnsRecords(),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (record: DnsRecordForm) => api.createDnsRecord(record),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dns-records'] });
      setIsFormOpen(false);
      setEditingRecord(null);
      toast.success('DNS record created');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to create DNS record'),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, record }: { id: number; record: Partial<DnsRecordForm> }) =>
      api.updateDnsRecord(id, record),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dns-records'] });
      setIsFormOpen(false);
      setEditingRecord(null);
      toast.success('DNS record updated');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to update DNS record'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDnsRecord(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dns-records'] });
      toast.success('DNS record deleted');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to delete DNS record'),
  });

  const handleAddValue = () => {
    setForm({ ...form, values: [...form.values, ''] });
  };

  const handleRemoveValue = (index: number) => {
    if (form.values.length <= 1) return;
    const newValues = [...form.values];
    newValues.splice(index, 1);
    setForm({ ...form, values: newValues });
  };

  const handleValueChange = (index: number, value: string) => {
    const newValues = [...form.values];
    newValues[index] = value;
    setForm({ ...form, values: newValues });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.domain.trim()) {
      toast.error('Domain is required');
      return;
    }
    if (form.record_type === 'CNAME') {
      if (!form.values[0]?.trim()) {
        toast.error('Target hostname is required for CNAME');
        return;
      }
    } else {
      const validIps = form.values.filter(v => v.trim());
      if (validIps.length === 0) {
        toast.error('At least one IP address is required');
        return;
      }
      setForm({ ...form, values: validIps });
    }

    if (editingRecord) {
      updateMutation.mutate({ id: editingRecord.id, record: form });
    } else {
      createMutation.mutate(form);
    }
  };

  const handleEdit = (record: DnsRecord) => {
    setEditingRecord(record);
    setIsFormOpen(true);
  };

  const handleCancel = () => {
    setIsFormOpen(false);
    setEditingRecord(null);
    setForm(emptyForm);
  };

  if (isLoading) {
    return <div className="text-slate-400 animate-pulse text-sm">Loading DNS records...</div>;
  }

  return (
    <div className="space-y-6">
      {isFormOpen && (
        <form onSubmit={handleSubmit} className="glass-card p-6 border border-white/10 space-y-4">
          <h4 className="text-sm font-bold text-white uppercase tracking-widest">
            {editingRecord ? 'Edit Record' : 'Add New Record'}
          </h4>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Domain</label>
              <input
                type="text"
                value={form.domain}
                onChange={(e) => setForm({ ...form, domain: e.target.value })}
                className="glass-input w-full text-sm"
                placeholder="hostname (e.g., ollama-server)"
                required
              />
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1">Record Type</label>
              <select
                value={form.record_type}
                onChange={(e) => setForm({ ...form, record_type: e.target.value as 'A' | 'CNAME' })}
                className="glass-input w-full text-sm"
              >
                <option value="A">A (IPv4)</option>
                <option value="CNAME">CNAME (Alias)</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">
              {form.record_type === 'CNAME' ? 'Target Hostname' : 'IP Addresses'}
              {form.record_type === 'A' && form.values.length > 1 && (
                <span className="ml-2 text-slate-500">(add multiple IPs for load balancing)</span>
              )}
            </label>
            {form.record_type === 'CNAME' ? (
              <input
                type="text"
                value={form.values[0] || ''}
                onChange={(e) => handleValueChange(0, e.target.value)}
                className="glass-input w-full text-sm"
                placeholder="target.local"
              />
            ) : (
              <div className="space-y-2">
                {form.values.map((value, index) => (
                  <div key={index} className="flex gap-2">
                    <input
                      type="text"
                      value={value}
                      onChange={(e) => handleValueChange(index, e.target.value)}
                      className="glass-input flex-1 text-sm"
                      placeholder={`IP address ${index + 1}`}
                    />
                    {form.values.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleRemoveValue(index)}
                        className="px-2 text-red-400 hover:text-red-300"
                      >
                        <X size={14} />
                      </button>
                    )}
                  </div>
                ))}
                <button
                  type="button"
                  onClick={handleAddValue}
                  className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
                >
                  <Plus size={12} /> Add another IP
                </button>
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">TTL (seconds)</label>
            <input
              type="number"
              value={form.ttl}
              onChange={(e) => setForm({ ...form, ttl: parseInt(e.target.value) || 300 })}
              className="glass-input w-32 text-sm"
              min={60}
              max={86400}
            />
          </div>

          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={handleCancel}
              className="glass-button px-4 py-2 text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending || updateMutation.isPending}
              className="glass-button px-4 py-2 text-sm bg-blue-600/20 border-blue-500/50"
            >
              {editingRecord ? <><Save size={14} /> Update</> : <><Plus size={14} /> Create</>}
            </button>
          </div>
        </form>
      )}

      <div className="flex justify-end">
        {!isFormOpen && (
          <button
            onClick={() => { setEditingRecord(null); setIsFormOpen(true); }}
            className="glass-button px-4 py-2 text-sm flex items-center gap-2"
          >
            <Plus size={16} /> Add DNS Record
          </button>
        )}
      </div>

      <div>
        <h4 className="flex items-center gap-2 text-sm font-bold text-slate-300 mb-4 uppercase tracking-widest">
          <Globe size={16} className="text-blue-400" />
          Active Records ({records.length})
        </h4>

        {records.length === 0 ? (
          <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
            No DNS records configured. Add a record above to get started.
          </div>
        ) : (
          <div className="space-y-2">
            {records.map((record) => (
              <div key={record.id} className="glass-card p-4 border border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <span className="px-2 py-1 text-xs font-bold text-blue-300 bg-blue-600/20 rounded">
                    {record.record_type}
                  </span>
                  <div>
                    <p className="text-sm font-mono text-white">{record.domain}</p>
                    <p className="text-xs text-slate-400">
                      {record.record_type === 'A'
                        ? record.values.join(', ')
                        : `→ ${record.values[0]}`}
                      {record.ttl !== 300 && ` (TTL: ${record.ttl}s)`}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleEdit(record)}
                    className="glass-button px-2 py-1 text-slate-400 hover:text-white"
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete DNS record for ${record.domain}?`)) {
                        deleteMutation.mutate(record.id);
                      }
                    }}
                    className="glass-button px-2 py-1 text-red-400 hover:text-red-300"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

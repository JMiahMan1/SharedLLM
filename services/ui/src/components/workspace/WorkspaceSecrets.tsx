import { useState } from 'react';
import { X, KeyRound, Plus, Trash2, Eye, EyeOff, Save, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type Workspace } from '../../services/api';

interface WorkspaceSecretsProps {
  workspace: Workspace;
  onClose: () => void;
}

interface SecretRow {
  key: string;
  value: string;
}

type WorkspaceEnvUpdate = Partial<Workspace> & {
  env?: Record<string, string>;
  env_delete?: string[];
};

/**
 * Per-workspace Secrets & Environment editor.
 *
 * Secrets are stored encrypted at rest on the server and injected into every
 * command run in the workspace's sandbox. Existing values are never returned to
 * the browser (only their key names), so the UI can add new secrets and delete
 * existing ones; editing an existing value is done by deleting + re-adding.
 */
export function WorkspaceSecrets({ workspace, onClose }: WorkspaceSecretsProps) {
  const existingKeys: string[] = (workspace as Workspace & { env_keys?: string[] }).env_keys ?? [];
  const [deleted, setDeleted] = useState<string[]>([]);
  const [additions, setAdditions] = useState<SecretRow[]>([]);
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  const visibleExisting = existingKeys.filter((k) => !deleted.includes(k));

  const addRow = () => {
    const k = newKey.trim();
    if (!k) return;
    setAdditions((prev) => [...prev, { key: k, value: newValue }]);
    setNewKey('');
    setNewValue('');
  };

  const removeAddition = (idx: number) => setAdditions((prev) => prev.filter((_, i) => i !== idx));
  const toggleDelete = (k: string) =>
    setDeleted((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));

  const save = async () => {
    setSaving(true);
    try {
      const env: Record<string, string> = {};
      for (const s of additions) {
        const k = s.key.trim();
        if (k) env[k] = s.value;
      }
      await api.updateWorkspace(workspace.id, { env, env_delete: deleted } as WorkspaceEnvUpdate);
      toast.success('Workspace secrets updated');
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error('Failed to save secrets: ' + msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-[#0d1222] border border-white/10 rounded-xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <div className="flex items-center gap-2 text-white font-semibold">
            <KeyRound size={18} className="text-indigo-400" /> Secrets &amp; Environment
          </div>
          <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded-lg">
            <X size={18} />
          </button>
        </div>

        <div className="p-4 space-y-4 text-sm">
          <p className="text-slate-400 text-xs">
            Per-workspace environment variables and secrets. These are encrypted at rest and
            injected into every command run in this workspace&apos;s sandbox, overriding the
            defaults from your connected integrations (GitHub, GitLab, Nextcloud, Home Assistant,
            …).
          </p>

          {visibleExisting.length > 0 && (
            <div>
              <div className="text-slate-300 mb-1">Existing secrets</div>
              <div className="space-y-1">
                {visibleExisting.map((k) => (
                  <div key={k} className="flex items-center justify-between bg-white/5 rounded px-2 py-1.5">
                    <span className="font-mono text-slate-200">{k}</span>
                    <button
                      onClick={() => toggleDelete(k)}
                      className="text-slate-400 hover:text-red-400 p-1"
                      title="Delete on save"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {deleted.length > 0 && (
            <div className="text-xs text-amber-400">
              {deleted.length} existing secret(s) will be removed on save.
            </div>
          )}

          <div>
            <div className="text-slate-300 mb-1">Add a secret</div>
            <div className="flex items-center gap-2">
              <input
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                placeholder="KEY"
                className="flex-1 bg-white/5 border border-white/10 rounded px-2 py-1.5 font-mono text-slate-200 outline-none focus:border-indigo-400"
              />
              <div className="flex-1 relative">
                <input
                  type={reveal['new'] ? 'text' : 'password'}
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  placeholder="value"
                  className="w-full bg-white/5 border border-white/10 rounded px-2 py-1.5 font-mono text-slate-200 outline-none focus:border-indigo-400"
                />
                <button
                  type="button"
                  onClick={() => setReveal((p) => ({ ...p, new: !p.new }))}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                >
                  {reveal['new'] ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              <button
                onClick={addRow}
                className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded"
                title="Add"
              >
                <Plus size={16} />
              </button>
            </div>
          </div>

          {additions.length > 0 && (
            <div className="space-y-1">
              <div className="text-slate-300">Pending additions</div>
              {additions.map((s, i) => (
                <div key={i} className="flex items-center justify-between bg-white/5 rounded px-2 py-1.5">
                  <span className="font-mono text-slate-200">{s.key}</span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setReveal((p) => ({ ...p, [`a${i}`]: !p[`a${i}`] }))}
                      className="text-slate-400 hover:text-white"
                    >
                      {reveal[`a${i}`] ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                    <span className="font-mono text-slate-500 text-xs">
                      {reveal[`a${i}`] ? s.value : '••••••••'}
                    </span>
                    <button onClick={() => removeAddition(i)} className="text-slate-400 hover:text-red-400 p-1">
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-white/10">
          <button onClick={onClose} className="px-3 py-1.5 rounded text-slate-300 hover:bg-white/10">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-indigo-500 hover:bg-indigo-400 text-white flex items-center gap-1.5 disabled:opacity-50"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save
          </button>
        </div>
      </div>
    </div>
  );
}

import { useState, FC } from 'react';
import { 
  Cloud, 
  Music, 
  Mic, 
  Lock,
  RefreshCcw,
  Home,
  Server,
  Save,
  X,
  Plus,
  Trash2,
  Edit3,
  CheckCircle,
  AlertCircle,
  Globe,
  LockKeyhole,
  Smartphone,
  User,
  Zap,
  Calendar,
  Shield,
  ListMusic,
  Watch,
  Copy
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import type { UserProfile, APIKey } from '../services/api';
import { useAuth } from '../context/AuthContext';
import toast from 'react-hot-toast';
import SecretInput from '../components/ui/SecretInput';
import SystemValueHint from '../components/ui/SystemValueHint';
import { credentialPayload, inUse, integrationFor, isSecretKey, missingFields, prefill, type Defaults } from '../lib/credentialForm';
import HelpTooltip from '../components/ui/HelpTooltip';
import { SharedCredentialsCard } from '../components/admin/SharedCredentialsCard';
import CompanionDevicesPanel from '../components/settings/CompanionDevicesPanel';
import VoiceSignaturePanel from '../components/settings/VoiceSignaturePanel';
import UserAvatar from '../components/UserAvatar';
import AvatarPicker from '../components/AvatarPicker';
import Modal from '../components/ui/Modal';

// Tailwind only ships classes it can see whole in the source, so the tile
// tints are spelled out rather than built from `bg-${color}-500/20`.
const TILE_TINT = {
  blue: 'bg-blue-500/20 text-blue-400',
  emerald: 'bg-emerald-500/20 text-emerald-400',
  amber: 'bg-amber-500/20 text-amber-400',
  fuchsia: 'bg-fuchsia-500/20 text-fuchsia-400',
  rose: 'bg-rose-500/20 text-rose-400',
  slate: 'bg-slate-500/20 text-slate-300',
  purple: 'bg-purple-500/20 text-purple-400',
} as const;

/** Services Identity's /api/auth/test-connection can actually check. */
const TESTABLE = new Set(['Home Assistant', 'Nextcloud', 'GitHub', 'GitLab', 'Audiobookshelf', 'Music Assistant']);

const NO_SERVICE_DEFAULTS = { urls: {} as Record<string, string | null>, source: {} as Record<string, string> };

interface IntegrationTileProps {
  name: string;
  icon: LucideIcon;
  color: keyof typeof TILE_TINT;
  configKeys: Record<string, string>;
  /** Fields that are arrays of strings, rendered as chip inputs */
  listKeys?: Record<string, string>;  // label -> field_key
  userData?: UserProfile | null;
}

const IntegrationTile: FC<IntegrationTileProps> = ({ name, icon: Icon, color, configKeys, listKeys, userData }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [listForm, setListForm] = useState<Record<string, string[]>>({});
  const [listInputs, setListInputs] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<{ status: 'SUCCESS' | 'ERROR', message?: string } | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [prefilled, setPrefilled] = useState<Defaults>({});
  const { data: serviceDefaults = NO_SERVICE_DEFAULTS } = useQuery({
    queryKey: ['service-defaults'],
    queryFn: () => api.getServiceDefaults(),
    staleTime: 300_000,
  });
  const isAdmin = useAuth().role === 'admin';
  const savedSecrets = new Set(userData?.saved_credentials ?? []);
  const fieldKeys = Object.values(configKeys);
  const known = fieldKeys.map(integrationFor).find(Boolean);
  const values = form as Record<string, unknown>;
  const labelOf = (key: string) => Object.keys(configKeys).find((label) => configKeys[label] === key) ?? key;
  const filled = (key: string) => String(values[key] ?? '').trim() !== '' || (isSecretKey(key) && savedSecrets.has(key));
  // What still blocks saving. A known integration follows the shared rules
  // (Audiobookshelf takes an API key *or* a username and password); anything
  // else needs every field. An untouched card has nothing to save.
  const missingList: string[] = known
    ? inUse(known, values, savedSecrets, prefilled)
      ? missingFields(known, values, savedSecrets, prefilled)
      : known.complete[0].filter((k) => !filled(k)).map(labelOf)
    : fieldKeys.filter((k) => k !== 'skylight_enabled' && !filled(k)).map(labelOf);
  const queryClient = useQueryClient();

  const connectionKey = Object.values(configKeys)[0];
  // Skylight is one household account kept on the system user; everyone else
  // only switches it on or off for themselves, so their own skylight_url is
  // always empty. Their status comes from Identity instead.
  const sharedSkylight = name === 'Skylight' && !userData?.is_system_default;
  const skylightStatus = userData?.skylight_status ?? 'not_set_up';
  const isConnected = sharedSkylight
    ? skylightStatus === 'connected'
    : !!(userData && (userData as Record<string, unknown>)[connectionKey]);
  const statusLine = sharedSkylight
    ? { connected: 'CONNECTED', off: 'TURNED OFF', not_set_up: 'NOT SET UP BY ADMIN' }[skylightStatus]
    : isConnected
      ? (userData as Record<string, string>)?.[connectionKey]?.replace(/^https?:\/\//, '')
      : 'DISCONNECTED';

  const updateMutation = useMutation({
    mutationFn: (data: Partial<UserProfile>) => api.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setIsOpen(false);
      toast.success(`${name} configuration updated`);
    },
    onError: (err: unknown) => {
      if (err instanceof Error) {
        toast.error(err.message || 'Update failed');
      } else {
        toast.error('Update failed');
      }
    }
  });

  const handleTest = async () => {
    setIsTesting(true);
    setTestResult(null);
    try {
      const result = await api.testConnection(name, form as Record<string, unknown>);
      setTestResult(result);
      if (result.status === 'SUCCESS') toast.success('Connection verified');
    } catch (err: unknown) {
      let message = 'Connection failed';
      if (err && typeof err === 'object' && 'response' in err) {
         const resp = (err as { response: { data: { detail?: string } } }).response;
         message = resp?.data?.detail || (err as unknown as Error).message || message;
      }
      setTestResult({ 
        status: 'ERROR', 
        message
      });
      toast.error('Connection failed');
    } finally {
      setIsTesting(false);
    }
  };

  const handleOpen = () => {
    const initialForm: Record<string, string | boolean> = {};
    Object.values(configKeys).forEach((key) => {
      // Secrets are never sent to the browser: their field starts empty and
      // shows "Saved"; empty on save means keep (see lib/credentialForm).
      initialForm[key] = isSecretKey(key) ? '' : (userData as Record<string, string | boolean>)?.[key] || '';
    });
    setConfirmDisconnect(false);
    // Blank addresses start from the household's; the Nextcloud username from yours.
    const start = prefill(initialForm, serviceDefaults.urls, userData?.username);
    Object.assign(initialForm, start.values);
    setPrefilled(start.prefilled);
    if (name === 'Skylight') {
      initialForm.skylight_enabled = userData?.skylight_enabled !== false;
    }
    setForm(initialForm);
    // Initialise list fields
    const initialListForm: Record<string, string[]> = {};
    Object.values(listKeys || {}).forEach((key) => {
      const raw = (userData as Record<string, unknown>)?.[key];
      initialListForm[key] = Array.isArray(raw) ? raw as string[] : [];
    });
    setListForm(initialListForm);
    setListInputs({});
    setIsOpen(true);
    setTestResult(null);
  };

  return (
    <>
      {/* A compact row on phones (nine full-height cards were a 3000px
          scroll), the original card from md up. */}
      <div className="glass-panel p-4 md:p-6 flex flex-row md:flex-col items-center text-left md:text-center gap-3 md:gap-4 group hover:border-purple-500/30 transition-all duration-500 hover:shadow-2xl hover:shadow-purple-500/10">
        <div className={`p-3 md:p-4 rounded-2xl shrink-0 ${TILE_TINT[color]} group-hover:scale-110 transition-transform duration-500`}>
          <Icon className="w-6 h-6 md:w-8 md:h-8" />
        </div>
        <div className="flex-1 md:flex-none min-w-0">
          <h3 className="font-bold text-white">{name}</h3>
          <p className="text-[10px] text-slate-500 mt-1 truncate md:max-w-[150px] font-mono" data-testid={`integration-status-${name}`}>
            {statusLine}
          </p>
        </div>
        <button onClick={handleOpen} aria-label={`${isConnected || sharedSkylight ? 'Manage' : 'Connect'} ${name}`} className="glass-button shrink-0 md:w-full text-[10px] uppercase font-bold tracking-widest md:mt-2 min-h-11 px-3">
          {sharedSkylight ? (skylightStatus === 'not_set_up' ? 'Details' : 'Turn on or off') : isConnected ? 'Manage' : 'Connect'}
        </button>
        <div className="hidden md:flex items-center gap-2 mt-2">
          <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-slate-600'}`} />
          <span className="text-[9px] uppercase font-black tracking-tighter text-slate-500">
            {sharedSkylight ? (isConnected ? 'Connected' : 'Not Connected') : isConnected ? 'Linked' : 'Not Linked'}
          </span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={name}>
        <div className="space-y-6">
          <div className="p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl flex gap-4">
             <LockKeyhole className="text-indigo-400 shrink-0" size={24} />
             <div className="text-xs text-slate-300 leading-relaxed">
               <p className="font-bold text-white mb-1">Stored encrypted</p>
               Passwords and tokens are encrypted at rest (Fernet) and never sent back to the browser; a saved one shows as Saved. Leave it blank to keep it.
             </div>
          </div>

          {name === 'Skylight' && !userData?.is_system_default && (
            <div className="p-4 bg-purple-500/10 border border-purple-500/20 rounded-xl flex gap-4">
               <Shield className="text-purple-400 shrink-0" size={24} />
               <div className="text-xs text-slate-300 leading-relaxed">
                 <p className="font-bold text-white mb-1">System Integration</p>
                 This is a system-wide integration configured by the administrator. You cannot edit these settings, but you can enable or disable Skylight chores and rewards for your account below.
               </div>
            </div>
          )}

          <div className="grid gap-4">
            {Object.entries(configKeys).map(([label, key]) => {
              const isSecret = isSecretKey(key);
              const isReadOnly = name === 'Skylight' && !userData?.is_system_default;

              return (
                <div key={key}>
                  <div className="flex items-center mb-2">
                    <label className="text-[10px] text-slate-400 uppercase font-black tracking-widest">{label}</label>
                    <HelpTooltip docName="INTEGRATIONS.md" sectionTitle={name} label={label} />
                  </div>
                  {isSecret ? (
                    <SecretInput
                      label={label}
                      value={String(form[key] ?? '')}
                      saved={savedSecrets.has(key)}
                      disabled={isReadOnly}
                      onReveal={userData?.username ? () => api.revealCredential(userData.username, key) : undefined}
                      onChange={(value) => setForm({ ...form, [key]: value })}
                      placeholder={isReadOnly ? 'Configured by system administrator' : `Enter ${label}...`}
                      className="text-sm py-3 bg-black/20 focus:bg-black/40"
                    />
                  ) : (
                    <>
                      <input
                        type="text"
                        value={String(form[key] ?? '')}
                        aria-label={label}
                        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                        disabled={isReadOnly}
                        className={`glass-input w-full text-sm py-3 bg-black/20 focus:bg-black/40 ${isReadOnly ? 'opacity-60 cursor-not-allowed' : ''}`}
                        placeholder={isReadOnly ? 'Configured by system administrator' : `Enter ${label}...`}
                      />
                      {serviceDefaults.urls[key] ? (
                        <SystemValueHint
                          value={String(form[key] ?? '')}
                          systemValue={String(serviceDefaults.urls[key])}
                          source={serviceDefaults.source[key]}
                          onReset={() => setForm({ ...form, [key]: String(serviceDefaults.urls[key]) })}
                        />
                      ) : (
                        prefilled[key] !== undefined &&
                        form[key] === prefilled[key] && <p className="mt-1 text-[10px] text-slate-500">Suggested from your account name</p>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          {name === 'Skylight' && (
            <div className="p-4 glass-card border-white/5 bg-white/5 rounded-xl flex items-center justify-between">
              <div>
                <p className="text-xs font-bold text-white">Enable Skylight for my Account</p>
                <p className="text-[10px] text-slate-500 mt-1">Toggle whether Skylight chores and rewards are active for your profile.</p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input 
                  type="checkbox" 
                  checked={form.skylight_enabled !== false}
                  onChange={(e) => setForm({...form, skylight_enabled: e.target.checked})}
                  className="sr-only peer" 
                />
                <div className="w-10 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
              </label>
            </div>
          )}

          {/* ── List / Chip fields ─────────────────────────────────── */}
          {listKeys && Object.entries(listKeys).map(([label, key]) => {
            const chips: string[] = listForm[key] || [];
            const addChip = (val: string) => {
              const trimmed = val.trim().toLowerCase().replace(/^[,\s]+|[,\s]+$/g, '');
              if (trimmed && !chips.includes(trimmed)) {
                setListForm(lf => ({ ...lf, [key]: [...(lf[key] || []), trimmed] }));
              }
              setListInputs(li => ({ ...li, [key]: '' }));
            };
            return (
              <div key={key} className="space-y-2">
                <div className="flex items-center mb-2">
                  <label className="text-[10px] text-slate-400 uppercase font-black tracking-widest">{label}</label>
                  <HelpTooltip docName="INTEGRATIONS.md" sectionTitle={name} label={label} />
                </div>
                {/* Chip display */}
                <div className="flex flex-wrap gap-2 min-h-[32px] p-2 glass-input rounded-xl bg-black/20">
                  {chips.map(chip => (
                    <span key={chip} className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-600/30 border border-purple-500/30 text-purple-300 text-[10px] font-bold">
                      {chip}
                      <button
                        type="button"
                        aria-label={`Remove ${chip}`}
                        onClick={() => setListForm(lf => ({ ...lf, [key]: lf[key].filter(c => c !== chip) }))}
                        className="text-purple-400 hover:text-red-400 transition-colors ml-0.5 p-1 -m-1"
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ))}
                </div>
                {/* Input to add new chip */}
                <input
                  type="text"
                  value={listInputs[key] || ''}
                  onChange={e => setListInputs(li => ({ ...li, [key]: e.target.value }))}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ',') {
                      e.preventDefault();
                      addChip(listInputs[key] || '');
                    }
                  }}
                  onBlur={() => { if (listInputs[key]?.trim()) addChip(listInputs[key]); }}
                  className="glass-input w-full text-sm py-2 bg-black/20 focus:bg-black/40"
                  placeholder={`Type branch name and press Enter…`}
                />
                <p className="text-[9px] text-slate-600">Jarvis will never push directly to these branches. Press Enter or comma to add.</p>
              </div>
            );
          })}

          {/* Home Assistant, Music Assistant, Audiobookshelf and Nextcloud can
              fall back to the system default user's credentials (per-service
              grant, admin-only). Skylight is system-shared by design, so it is
              not listed here. */}
          {name !== 'Skylight' && name !== 'OpenRouter' && !!userData?.username && (
            <SharedCredentialsCard
              username={userData.username}
              isAdmin={isAdmin}
              isSystemDefault={!!userData.is_system_default}
            />
          )}

          {testResult && (
            <div className={`p-4 rounded-xl flex items-center gap-3 border ${
              testResult.status === 'SUCCESS' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border-red-500/20 text-red-400'
            }`}>
              {testResult.status === 'SUCCESS' ? <CheckCircle size={18} /> : <AlertCircle size={18} />}
              <p className="text-xs font-medium">{testResult.message || (testResult.status === 'SUCCESS' ? 'Connection Successful' : 'Connection Failed')}</p>
            </div>
          )}

          <div className="flex gap-3 pt-4">
            {TESTABLE.has(name) && (
              <button 
                onClick={handleTest}
                disabled={isTesting}
                className="glass-button px-6 py-3 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
              >
                <RefreshCcw size={16} className={isTesting ? 'animate-spin' : ''} />
                {isTesting ? 'Testing...' : 'Test'}
              </button>
            )}
            <button 
              onClick={() => {
                if (missingList.length) {
                  toast.error(`Fill in ${missingList.join(', ')} before saving`);
                  return;
                }
                // Only filled fields: blank keeps what is saved.
                const finalForm: Record<string, unknown> = {
                  ...credentialPayload(form as Record<string, unknown>, fieldKeys, [], { saved: savedSecrets, prefilled }),
                  ...listForm,
                };
                if (typeof form.skylight_enabled === 'boolean') finalForm.skylight_enabled = form.skylight_enabled;
                if (name === 'Skylight' && !userData?.is_system_default) {
                  delete finalForm.skylight_url;
                  delete finalForm.skylight_email;
                  delete finalForm.skylight_pass;
                }
                updateMutation.mutate(finalForm as Partial<UserProfile>);
              }}
              disabled={updateMutation.isPending || missingList.length > 0}
              title={missingList.length ? `Still needed: ${missingList.join(', ')}` : undefined}
              className="glass-button flex-1 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest"
            >
              <Save size={18} />
              {updateMutation.isPending ? 'Saving...' : 'Save'}
            </button>
          </div>
          {missingList.length > 0 && (
            <p className="text-[11px] text-amber-300">Still needed: {missingList.join(', ')}</p>
          )}
          {isConnected && !(name === 'Skylight' && !userData?.is_system_default) && (
            <div className="border-t border-white/5 pt-3">
              {confirmDisconnect ? (
                <div className="flex flex-wrap items-center gap-3 text-xs text-rose-200" role="alert">
                  Remove your {name} login from Jarvis?
                  <button
                    type="button"
                    className="font-semibold underline min-h-11 px-2"
                    onClick={() => updateMutation.mutate({ clear_fields: fieldKeys.filter((k) => k !== 'skylight_enabled') } as Partial<UserProfile>)}
                  >
                    Remove
                  </button>
                  <button type="button" className="text-slate-300 min-h-11 px-2" onClick={() => setConfirmDisconnect(false)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button type="button" className="text-xs text-rose-300 hover:underline min-h-11" onClick={() => setConfirmDisconnect(true)}>
                  Disconnect {name}
                </button>
              )}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
};

const APIKeyRows = () => {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { data: keys, isLoading } = useQuery<APIKey[]>({
    queryKey: ['api-keys'],
    queryFn: () => api.getAPIKeys(),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string | number) => api.revokeAPIKey(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success('Key access revoked');
    },
    onError: () => toast.error('Revocation failed')
  });

  const [confirmRevoke, setConfirmRevoke] = useState<string | number | null>(null);
  const isAdmin = user?.is_admin || false;
  const hasKeys = keys?.length;

  if (isLoading) return <tr><td colSpan={4} className="px-6 py-8 text-center text-slate-500 animate-pulse">Loading keys…</td></tr>;
  if (!hasKeys) return <tr><td colSpan={4} className="px-6 py-8 text-center text-slate-500 italic">No API keys yet.</td></tr>;

  return (
    <>
      {keys?.map((k) => (
        <tr key={k.id} className="group hover:bg-white/5 transition-colors">
           <td className="px-4 md:px-6 py-4">
              <div className="flex items-center gap-3 min-w-0">
                 <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-400 shrink-0"><Smartphone size={14} /></div>
                 <div className="min-w-0">
                   <span className="block font-bold text-white tracking-tight break-words">{k.label}</span>
                   {/* Phones: the prefix column is hidden, so show it here. */}
                   <span className="sm:hidden block font-mono text-slate-500 text-[10px]">••••{k.prefix}</span>
                 </div>
              </div>
           </td>
           <td className="hidden sm:table-cell px-6 py-4 font-mono text-slate-500 text-[10px]">sk-jarvis-••••{k.prefix}</td>
           <td className="hidden sm:table-cell px-6 py-4">
              <span className={`px-2 py-0.5 rounded-full text-[8px] font-black uppercase tracking-widest ${isAdmin ? 'bg-purple-500/10 text-purple-400 border border-purple-500/20' : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'}`}>
                {isAdmin ? `Owner: ${k.owner_username}` : 'Active'}
              </span>
           </td>
           <td className="px-4 md:px-6 py-4 text-right">
              {confirmRevoke === k.id ? (
                <span className="inline-flex items-center gap-1">
                  <button
                    onClick={() => { revokeMutation.mutate(k.id); setConfirmRevoke(null); }}
                    className="min-h-11 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest text-red-300 bg-red-500/10 border border-red-500/30"
                  >
                    Revoke
                  </button>
                  <button onClick={() => setConfirmRevoke(null)} className="min-h-11 px-2 text-[10px] text-slate-400">
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  onClick={() => setConfirmRevoke(k.id)}
                  aria-label={`Revoke ${k.label}`}
                  className="inline-flex items-center justify-center w-11 h-11 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all"
                >
                   <Trash2 size={16} />
                </button>
              )}
           </td>
        </tr>
      ))}
    </>
  );
};

/**
 * New-key flow: a label field, then the key shown once with a copy button.
 * It used window.prompt() for both, which on the Android app is a bare
 * WebView dialog and on desktop offered no way to see the key if the
 * clipboard write was refused.
 */
const NewApiKey = () => {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState<string | null>(null);
  const [created, setCreated] = useState<{ label: string; key: string } | null>(null);
  const generate = useMutation({
    mutationFn: (name: string) => api.generateAPIKey(name),
    onSuccess: (res, name) => {
      setLabel(null);
      setCreated({ label: name, key: res.key || '' });
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
    onError: () => toast.error('Failed to generate key'),
  });
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created?.key ?? '');
      toast.success('Key copied');
    } catch {
      toast.error('Copy failed: select the key and copy it by hand');
    }
  };

  return (
    <>
      <button
        onClick={() => setLabel('')}
        className="glass-button text-[10px] min-h-11 px-4 bg-emerald-600/20 text-emerald-400 border-emerald-500/20"
      >
        <Plus size={14} /> Generate New Key
      </button>
      <Modal isOpen={label !== null} onClose={() => setLabel(null)} title="New API key" size="md">
        <form
          className="space-y-4"
          onSubmit={(e) => { e.preventDefault(); if (label?.trim()) generate.mutate(label.trim()); }}
        >
          <label className="block text-xs text-slate-400" htmlFor="new-key-label">Which client is this for?</label>
          <input
            id="new-key-label"
            autoFocus
            value={label ?? ''}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. OpenWebUI"
            className="glass-input w-full text-sm"
          />
          <button
            type="submit"
            disabled={!label?.trim() || generate.isPending}
            className="glass-button w-full min-h-11 bg-emerald-600/30 text-emerald-200 disabled:opacity-50"
          >
            {generate.isPending ? 'Generating…' : 'Generate'}
          </button>
        </form>
      </Modal>
      <Modal isOpen={created !== null} onClose={() => setCreated(null)} title={`Key for ${created?.label ?? ''}`} size="md">
        <div className="space-y-4">
          <p className="text-xs text-amber-300">Copy it now: it will not be shown again.</p>
          <code data-testid="new-api-key" className="block p-3 rounded-xl bg-black/40 text-xs text-emerald-300 break-all select-all">
            {created?.key}
          </code>
          <button onClick={copy} className="glass-button w-full min-h-11">
            <Copy size={14} /> Copy key
          </button>
        </div>
      </Modal>
    </>
  );
};

const Identity = () => {
  const [pickingAvatar, setPickingAvatar] = useState(false);
  const { user } = useAuth();
  const queryClient = useQueryClient();
  
  const { data: fullUser } = useQuery<UserProfile>({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
    enabled: !!user
  });

  const { data: usersList } = useQuery<UserProfile[]>({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: !!fullUser?.is_admin
  });

  const updateProfileMutation = useMutation({
    mutationFn: (data: Partial<UserProfile>) => api.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      toast.success('Name saved');
    },
    onError: () => toast.error('Could not save your name'),
  });

  return (
    <div className="space-y-8 md:space-y-12 pb-12">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div>
          <h2 className="text-2xl md:text-4xl font-black text-white tracking-tight">Accounts & Integrations</h2>
          <p className="text-slate-400 mt-2 max-w-md">Your profile, the services Jarvis signs in to for you, your voice, devices and API keys.</p>
        </div>
        <div className="flex items-center gap-4 bg-white/5 p-3 rounded-2xl border border-white/5">
           <div className="w-12 h-12 rounded-xl bg-purple-600 flex items-center justify-center text-xl font-black text-white shadow-lg shadow-purple-500/20">
              {user?.username?.[0]?.toUpperCase()}
           </div>
           <div>
              <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest">Signed in as</p>
              <p className="text-white font-mono font-bold text-sm">@{user?.username}</p>
           </div>
        </div>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        <div className="xl:col-span-2 space-y-12">
          <section>
            <div className="flex items-center gap-4 mb-8">
              <div className="p-3 rounded-2xl bg-indigo-500/20 text-indigo-400 shadow-inner">
                <RefreshCcw size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">Integration Gallery</h3>
                <p className="text-sm text-slate-400">Secure connection points for cloud and home services</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-6">
              <IntegrationTile 
                name="Home Assistant" 
                icon={Home} 
                color="blue" 
                userData={fullUser}
                configKeys={{
                  "Instance URL": "ha_url",
                  "Long-Lived Token": "ha_token"
                }}
              />
              <IntegrationTile 
                name="Nextcloud" 
                icon={Cloud} 
                color="emerald" 
                userData={fullUser}
                configKeys={{
                  "Cloud Base URL": "nextcloud_url",
                  "Cloud Username": "nextcloud_user",
                  "App Password": "nextcloud_pass"
                }}
              />
              <IntegrationTile 
                name="Audiobookshelf" 
                icon={Music} 
                color="amber" 
                userData={fullUser}
                configKeys={{
                  "Server URL": "audiobookshelf_url",
                  "Username": "audiobookshelf_user",
                  "Password": "audiobookshelf_pass",
                  "API Key": "audiobookshelf_api_key"
                }}
              />
              <IntegrationTile 
                name="Music Assistant" 
                icon={ListMusic} 
                color="fuchsia" 
                userData={fullUser}
                configKeys={{
                  "Server URL": "mass_url",
                  "Access Token": "mass_token"
                }}
              />
              <IntegrationTile 
                name="Private Git" 
                icon={Server} 
                color="rose" 
                userData={fullUser}
                configKeys={{
                  "Git Endpoint": "git_url",
                  "Auth Username": "git_user",
                  "Auth Password": "git_token"
                }}
              />
              <IntegrationTile 
                name="GitHub" 
                icon={Globe} 
                color="slate" 
                userData={fullUser}
                configKeys={{
                  "GitHub Username": "github_user",
                  "Personal Access Token": "github_token"
                }}
                listKeys={{
                  "Protected Branches (no direct push)": "forbidden_branches"
                }}
              />
              <IntegrationTile 
                name="GitLab" 
                icon={Server} 
                color="rose" 
                userData={fullUser}
                configKeys={{
                  "GitLab URL": "gitlab_url",
                  "GitLab Username": "gitlab_user",
                  "Access Token": "gitlab_token"
                }}
              />
              <IntegrationTile 
                name="Skylight" 
                icon={Calendar} 
                color="purple" 
                userData={fullUser}
                configKeys={{
                  "Skylight Base URL": "skylight_url",
                  "Login Username/Email": "skylight_email",
                  "Password": "skylight_pass"
                }}
              />
              {!fullUser?.is_system_default && (
                <IntegrationTile 
                  name="OpenRouter" 
                  icon={Zap} 
                  color="purple" 
                  userData={fullUser}
                  configKeys={{
                    "Cloud Base URL": "openrouter_url",
                    "Cloud API Key": "openrouter_token"
                  }}
                />
              )}
            </div>
          </section>

          <section>
            <div className="flex items-center gap-3 mb-8">
              <div className="p-3 rounded-2xl bg-pink-500/20 text-pink-400">
                <Mic size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">Vocal Signature</h3>
                <p className="text-sm text-slate-400">So Jarvis can tell who in the family is speaking</p>
              </div>
            </div>
            
            <div className="max-w-3xl">
               <VoiceSignaturePanel />
            </div>
          </section>

          <section>
            <div className="flex items-center gap-4 mb-8">
              <div className="p-3 rounded-2xl bg-sky-500/20 text-sky-400">
                <Watch size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">Devices</h3>
                <p className="text-sm text-slate-400">Your phone, watch and assistants. Devices you add are linked to you.</p>
              </div>
            </div>
            <CompanionDevicesPanel />
          </section>

          <section>
            <div className="flex items-center gap-4 mb-8">
              <div className="p-3 rounded-2xl bg-emerald-500/20 text-emerald-400">
                <Lock size={24} />
              </div>
              <div className="flex items-center gap-2">
                <div>
                  <h3 className="text-xl font-bold text-white flex items-center gap-2">
                    External Client Access
                    <HelpTooltip docName="INTEGRATIONS.md" sectionTitle="External Client Setup" label="API Keys" />
                  </h3>
                  <p className="text-sm text-slate-400">Manage API keys for OpenWebUI, Lobechat, and other compatible interfaces</p>
                </div>
              </div>
            </div>
            
            <div className="glass-panel overflow-hidden border-emerald-500/10">
              <div className="p-4 md:p-6 border-b border-white/5 flex flex-wrap gap-3 items-center justify-between bg-emerald-500/5">
                 <div className="flex items-center gap-2">
                    <Globe size={16} className="text-emerald-400" />
                    <span className="text-[10px] font-black uppercase tracking-widest text-emerald-400">OpenAI-Compatible Endpoints</span>
                 </div>
                 <NewApiKey />
              </div>
              
              <div className="p-0">
                 <table className="w-full text-left text-xs">
                    <thead className="bg-white/5 text-slate-500 font-black uppercase text-[9px] tracking-widest">
                       <tr>
                          <th className="px-4 md:px-6 py-4">Client Label</th>
                          <th className="hidden sm:table-cell px-6 py-4">API Key Prefix</th>
                          <th className="hidden sm:table-cell px-6 py-4">Status</th>
                          <th className="px-4 md:px-6 py-4 text-right">Actions</th>
                       </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                       <APIKeyRows />
                    </tbody>
                 </table>
              </div>
            </div>
          </section>
        </div>

        <div className="space-y-8">
          <section className="glass-panel p-6 md:p-8 bg-purple-900/10 border-purple-500/20">
             <div className="flex items-center gap-3 mb-6">
                <User size={20} className="text-purple-400" />
                <h3 className="font-bold text-white uppercase text-xs tracking-widest">Profile</h3>
             </div>
             
             <div className="space-y-6">
                <div className="flex flex-col items-center">
                   <button
                      type="button"
                      onClick={() => setPickingAvatar(true)}
                      disabled={!fullUser}
                      aria-label="Change profile picture"
                      data-testid="avatar-open"
                      className="w-24 h-24 rounded-full flex items-center justify-center relative group overflow-hidden mb-4"
                   >
                      {fullUser ? (
                        <UserAvatar username={fullUser.username} avatar={fullUser.avatar} name={fullUser.full_name || fullUser.username} size={96} />
                      ) : (
                        <Plus size={32} className="text-slate-600" />
                      )}
                      <span className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 flex items-center justify-center transition-opacity rounded-full">
                         <Edit3 size={20} className="text-white" />
                      </span>
                   </button>
                   {pickingAvatar && fullUser && <AvatarPicker user={fullUser} onClose={() => setPickingAvatar(false)} />}
                   <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest mb-1">Tap to change your picture</p>
                   <p className="text-white font-bold">{fullUser?.full_name || 'Set Full Name'}</p>
                </div>

                <div className="space-y-4 pt-4 border-t border-white/5">
                   <div>
                      <label className="text-[9px] text-slate-500 uppercase font-black block mb-2 tracking-widest">Display Name</label>
                      {/* Keyed on the loaded name: defaultValue is read once, so
                          before the profile arrived the box stayed empty and
                          blurring it saved an empty name over the real one. */}
                      <input
                        key={fullUser ? `name-${fullUser.full_name ?? ''}` : 'name-loading'}
                        type="text"
                        aria-label="Display name"
                        defaultValue={fullUser?.full_name ?? ''}
                        disabled={!fullUser}
                        onBlur={(e) => {
                          const next = e.target.value.trim();
                          if (fullUser && next && next !== (fullUser.full_name ?? '')) {
                            updateProfileMutation.mutate({ full_name: next });
                          }
                        }}
                        className="glass-input w-full text-sm"
                        placeholder="Display name"
                      />
                   </div>
                </div>
             </div>
          </section>

          {fullUser?.is_admin && (
            <section className="glass-panel p-6 border-blue-500/20">
               <h3 className="font-bold text-white mb-4 text-xs tracking-widest uppercase">Family accounts</h3>
               <div className="space-y-3">
                  {usersList?.map((u) => (
                    <div key={u.id} className="flex items-center justify-between p-3 glass-card text-xs">
                       <div className="flex items-center gap-3">
                          <div className={`w-2 h-2 rounded-full ${u.is_admin ? 'bg-indigo-500 shadow-[0_0_8px_rgba(99,102,241,0.6)]' : 'bg-slate-600'}`} />
                          <span className="font-bold text-slate-300">@{u.username}</span>
                       </div>
                       <span className="text-[8px] uppercase font-black px-2 py-0.5 rounded-full bg-white/5 text-slate-500">{u.is_admin ? 'Admin' : 'User'}</span>
                    </div>
                  ))}
               </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
};

export default Identity;

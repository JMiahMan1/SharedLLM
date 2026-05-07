import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Cpu,
  Edit3,
  KeyRound,
  RefreshCcw,
  Save,
  Search,
  Settings2,
  Shield,
  Trash2,
  UserPlus,
  Database,
  Cloud,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type {
  DeviceAssignment,
  DiscoveredUser,
  GlobalSetting,
  LogEntry,
  UserProfile,
  RagStats,
} from '../services/api';
import Modal from '../components/ui/Modal';
import HelpTooltip from '../components/ui/HelpTooltip';

type UserFormState = {
  username: string;
  full_name: string;
  is_admin: boolean;
  ha_url: string;
  ha_token: string;
  nextcloud_url: string;
  nextcloud_user: string;
  nextcloud_pass: string;
  github_url: string;
  github_user: string;
  github_token: string;
  gitlab_url: string;
  gitlab_user: string;
  gitlab_token: string;
  audiobookshelf_url: string;
  audiobookshelf_user: string;
  audiobookshelf_pass: string;
};

const emptyUserForm: UserFormState = {
  username: '',
  full_name: '',
  is_admin: false,
  ha_url: '',
  ha_token: '',
  nextcloud_url: '',
  nextcloud_user: '',
  nextcloud_pass: '',
  github_url: '',
  github_user: '',
  github_token: '',
  gitlab_url: '',
  gitlab_user: '',
  gitlab_token: '',
  audiobookshelf_url: '',
  audiobookshelf_user: '',
  audiobookshelf_pass: '',
};

const toUserForm = (user?: UserProfile | null): UserFormState => ({
  username: user?.username ?? '',
  full_name: user?.full_name ?? '',
  is_admin: Boolean(user?.is_admin),
  ha_url: String(user?.ha_url ?? ''),
  ha_token: '',
  nextcloud_url: String(user?.nextcloud_url ?? ''),
  nextcloud_user: String(user?.nextcloud_user ?? ''),
  nextcloud_pass: '',
  github_url: String(user?.github_url ?? ''),
  github_user: String(user?.github_user ?? ''),
  github_token: '',
  gitlab_url: String(user?.gitlab_url ?? ''),
  gitlab_user: String(user?.gitlab_user ?? ''),
  gitlab_token: '',
  audiobookshelf_url: String(user?.audiobookshelf_url ?? ''),
  audiobookshelf_user: String(user?.audiobookshelf_user ?? ''),
  audiobookshelf_pass: '',
});

const Admin = () => {
  const queryClient = useQueryClient();
  const [isUserModalOpen, setIsUserModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<UserProfile | null>(null);
  const [userForm, setUserForm] = useState<UserFormState>(emptyUserForm);
  const [deviceId, setDeviceId] = useState('');
  const [deviceUsername, setDeviceUsername] = useState('');
  const [settingsDrafts, setSettingsDrafts] = useState<Record<string, string>>({});
  const [newSettingKey, setNewSettingKey] = useState('');
  const [newSettingValue, setNewSettingValue] = useState('');
  const [discoveryFilter, setDiscoveryFilter] = useState('');
  const [passwordModalUser, setPasswordModalUser] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState('');
  const [inspectingCollection, setInspectingCollection] = useState<string | null>(null);
  const [inspectLimit, setInspectLimit] = useState(50);

  const { data: users = [] } = useQuery<UserProfile[]>({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
  });

  const { data: discoveredUsers = [], refetch: refetchDiscoveredUsers, isFetching: isDiscovering } = useQuery<DiscoveredUser[]>({
    queryKey: ['discovered-users'],
    queryFn: () => api.discoverUsers(),
  });

  const { data: devices = [] } = useQuery<DeviceAssignment[]>({
    queryKey: ['devices'],
    queryFn: () => api.getDevices(),
  });

  const { data: settings = [] } = useQuery<GlobalSetting[]>({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
  });

  const { data: logs = [] } = useQuery<LogEntry[]>({
    queryKey: ['admin-logs'],
    queryFn: () => api.getLogs(12),
    refetchInterval: 10000,
  });
  
  const { data: ragStats } = useQuery<RagStats>({
    queryKey: ['rag-stats'],
    queryFn: () => api.getRagStats(),
  });

  const { data: collectionDocs, isFetching: isFetchingDocs } = useQuery({
    queryKey: ['collection-docs', inspectingCollection, inspectLimit],
    queryFn: () => inspectingCollection ? api.getCollectionDocs(inspectingCollection, inspectLimit) : null,
    enabled: !!inspectingCollection,
  });

  const saveUserMutation = useMutation({
    mutationFn: async (form: UserFormState) => {
      const payload = {
        username: form.username.trim().toLowerCase(),
        full_name: form.full_name,
        is_admin: form.is_admin,
        ha_url: form.ha_url,
        ha_token: form.ha_token,
        nextcloud_url: form.nextcloud_url,
        nextcloud_user: form.nextcloud_user,
        nextcloud_pass: form.nextcloud_pass,
        github_url: form.github_url,
        github_user: form.github_user,
        github_token: form.github_token,
        gitlab_url: form.gitlab_url,
        gitlab_user: form.gitlab_user,
        gitlab_token: form.gitlab_token,
        audiobookshelf_url: form.audiobookshelf_url,
        audiobookshelf_user: form.audiobookshelf_user,
        audiobookshelf_pass: form.audiobookshelf_pass,
      };

      if (editingUser) {
        return api.updateUser(editingUser.username, payload);
      }
      return api.createUser(payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setEditingUser(null);
      setUserForm(emptyUserForm);
      setIsUserModalOpen(false);
      toast.success('User saved');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save user'),
  });

  const deleteUserMutation = useMutation({
    mutationFn: (username: string) => api.deleteUser(username),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success('User deleted');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete user'),
  });

  const importUserMutation = useMutation({
    mutationFn: (user: DiscoveredUser) => api.createUser({
      username: user.username,
      full_name: user.display_name || user.username,
      is_admin: false,
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['users'] }),
        queryClient.invalidateQueries({ queryKey: ['discovered-users'] }),
      ]);
      toast.success('Discovered user imported');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to import user'),
  });

  const syncDiscoveryMutation = useMutation({
    mutationFn: () => api.syncDiscovery(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      toast.success(`Discovery synced ${data.entities_count} entities`);
    },
    onError: (error: Error) => toast.error(error.message || 'Discovery sync failed'),
  });

  const assignDeviceMutation = useMutation({
    mutationFn: (payload: { username: string; device_id: string }) => api.updateDeviceAssignment(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      setDeviceId('');
      toast.success('Device assignment updated');
    },
    onError: (error: Error) => toast.error(error.message || 'Device assignment failed'),
  });

  const removeDeviceMutation = useMutation({
    mutationFn: (targetDeviceId: string) => api.deleteDeviceAssignment(targetDeviceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      toast.success('Device assignment removed');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to remove assignment'),
  });

  const saveSettingMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => api.updateSetting(key, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      toast.success('Setting saved');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save setting'),
  });
  
  const setPasswordMutation = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) => api.adminSetPassword(username, password),
    onSuccess: () => {
      setPasswordModalUser(null);
      setNewPassword('');
      toast.success('Password updated successfully');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to update password'),
  });

  const importNcMutation = useMutation({
    mutationFn: () => api.importNextcloudUsers(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success(data.message || 'Users imported from Nextcloud');
    },
    onError: (error: Error) => toast.error(error.message || 'Nextcloud import failed'),
  });

  const filteredDiscoveredUsers = useMemo(() => {
    return discoveredUsers.filter((user) => {
      const haystack = `${user.username} ${user.display_name ?? ''} ${user.source}`.toLowerCase();
      return haystack.includes(discoveryFilter.toLowerCase());
    });
  }, [discoveredUsers, discoveryFilter]);

  const openCreateUser = () => {
    setEditingUser(null);
    setUserForm(emptyUserForm);
    setIsUserModalOpen(true);
  };

  const openEditUser = (user: UserProfile) => {
    setEditingUser(user);
    setUserForm(toUserForm(user));
    setIsUserModalOpen(true);
  };

  return (
    <div className="space-y-8 pb-12">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tighter text-white uppercase">System Matrix</h2>
          <p className="mt-2 text-slate-400">Live user administration, device ownership, settings, and audit state.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => syncDiscoveryMutation.mutate()}
            disabled={syncDiscoveryMutation.isPending}
            className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
          >
            <RefreshCcw size={14} className={syncDiscoveryMutation.isPending ? 'animate-spin' : ''} />
            Sync Device Discovery
          </button>
          <button
            onClick={openCreateUser}
            className="glass-button px-4 py-3 bg-indigo-600/30 border-indigo-500/30 text-[10px] font-black uppercase tracking-widest text-indigo-300"
          >
            <UserPlus size={14} />
            Create User
          </button>
        </div>
      </header>

      <div className="grid gap-6 xl:gap-8 xl:grid-cols-[1.2fr_0.8fr]">
        <section className="glass-panel p-6 min-w-0 overflow-hidden">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h3 className="flex items-center gap-3 text-xl font-bold text-white">
                <Shield size={20} className="text-indigo-400" />
                User Management
              </h3>
              <p className="mt-1 text-sm text-slate-400">Create, edit, and remove real identities used by the backend.</p>
            </div>
            <HelpTooltip docName="api_reference.md" sectionTitle="Identity Resolution" label="Users" />
          </div>

          <div className="space-y-3">
            {users.map((user) => (
              <div key={user.username} className="glass-card flex items-center justify-between p-4 gap-4 overflow-hidden">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-3">
                    <p className="font-semibold text-white">{user.full_name || user.username}</p>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-black uppercase tracking-widest ${
                      user.is_admin ? 'bg-indigo-500/10 text-indigo-300' : 'bg-white/5 text-slate-400'
                    }`}>
                      {user.is_admin ? 'Admin' : 'User'}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">@{user.username}</p>
                  <p className="mt-2 text-[11px] text-slate-500 truncate">
                    HA: {user.ha_url || 'Not configured'} | Nextcloud: {user.nextcloud_url || 'Not configured'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEditUser(user)}
                    className="rounded-xl p-2 text-slate-400 transition hover:bg-white/5 hover:text-white"
                    aria-label={`Edit ${user.username}`}
                  >
                    <Edit3 size={16} />
                  </button>
                  <button
                    onClick={() => setPasswordModalUser(user.username)}
                    className="rounded-xl p-2 text-slate-400 transition hover:bg-indigo-500/10 hover:text-indigo-300"
                    aria-label={`Change password for ${user.username}`}
                  >
                    <KeyRound size={16} />
                  </button>
                  {!user.is_system_default && (
                    <button
                      onClick={() => deleteUserMutation.mutate(user.username)}
                      className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                      aria-label={`Delete ${user.username}`}
                    >
                      <Trash2 size={16} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="glass-panel p-6 min-w-0 overflow-hidden">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h3 className="flex items-center gap-3 text-xl font-bold text-white">
                <Search size={20} className="text-emerald-400" />
                Discovery Import
              </h3>
              <p className="mt-1 text-sm text-slate-400">Live Home Assistant and Nextcloud discovery results.</p>
            </div>
            <div className="flex gap-2">
              <button 
                onClick={() => importNcMutation.mutate()}
                disabled={importNcMutation.isPending}
                className="rounded-xl p-2 text-slate-400 transition hover:bg-white/5 hover:text-white"
                title="Sync users from Nextcloud OCS"
              >
                <Cloud size={16} className={importNcMutation.isPending ? 'animate-pulse' : ''} />
              </button>
              <button
                onClick={() => refetchDiscoveredUsers()}
                className="rounded-xl p-2 text-slate-400 transition hover:bg-white/5 hover:text-white"
                aria-label="Refresh discovered users"
              >
                <RefreshCcw size={16} className={isDiscovering ? 'animate-spin' : ''} />
              </button>
            </div>
          </div>

          <input
            type="text"
            value={discoveryFilter}
            onChange={(event) => setDiscoveryFilter(event.target.value)}
            placeholder="Filter discovered users"
            className="glass-input mb-4 w-full"
          />

          <div className="space-y-3">
            {filteredDiscoveredUsers.map((user) => (
              <div key={`${user.source}-${user.username}`} className="glass-card flex items-center justify-between p-4">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-white truncate">{user.display_name || user.username}</p>
                  <p className="text-xs text-slate-400 truncate">@{user.username}</p>
                  <p className="mt-1 text-[11px] uppercase tracking-widest text-slate-500">{user.source}</p>
                </div>
                <button
                  onClick={() => importUserMutation.mutate(user)}
                  className="glass-button px-3 py-2 text-[10px] font-black uppercase tracking-widest"
                >
                  Import
                </button>
              </div>
            ))}
            {!filteredDiscoveredUsers.length && (
              <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                No undiscovered users matched the current filter.
              </p>
            )}
          </div>
        </section>
      </div>

      <div className="grid gap-6 xl:gap-8 xl:grid-cols-[0.95fr_1.05fr]">
        <section className="glass-panel p-6 min-w-0 overflow-hidden">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h3 className="flex items-center gap-3 text-xl font-bold text-white">
                <Cpu size={20} className="text-orange-300" />
                Device Assignments
              </h3>
              <p className="mt-1 text-sm text-slate-400">Every entry here is live identity-to-device data.</p>
            </div>
            <HelpTooltip docName="architecture.md" sectionTitle="Identity Service" label="Device Assignments" />
          </div>

          <div className="mb-4 grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1fr_220px_auto]">
            <input
              type="text"
              value={deviceId}
              aria-label="Device ID"
              onChange={(event) => setDeviceId(event.target.value)}
              placeholder="Home Assistant entity ID"
              className="glass-input"
            />
            <select
              value={deviceUsername}
              aria-label="Device User"
              onChange={(event) => setDeviceUsername(event.target.value)}
              className="glass-input bg-black/30"
            >
              <option value="">Select user</option>
              {users.map((user) => (
                <option key={user.username} value={user.username}>
                  {user.username}
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                if (!deviceId.trim() || !deviceUsername) {
                  toast.error('Choose a user and a device ID');
                  return;
                }
                assignDeviceMutation.mutate({ username: deviceUsername, device_id: deviceId.trim() });
              }}
              className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <Save size={14} />
              Save Assignment
            </button>
          </div>

          <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-2">
            {devices.map((device) => (
              <div key={device.device_id} className="glass-card flex items-center justify-between p-4">
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-sm text-white truncate">{device.device_id}</p>
                  <p className="mt-1 text-xs text-slate-400">Assigned to @{device.username}</p>
                </div>
                <button
                  onClick={() => removeDeviceMutation.mutate(device.device_id)}
                  className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                  aria-label={`Remove ${device.device_id}`}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-8 min-w-0 overflow-hidden">
          <div className="glass-panel p-6">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h3 className="flex items-center gap-3 text-xl font-bold text-white">
                  <Settings2 size={20} className="text-cyan-300" />
                  Global Settings
                </h3>
                <p className="mt-1 text-sm text-slate-400">Seeded system settings with live writeback.</p>
              </div>
              <HelpTooltip docName="architecture.md" sectionTitle="Global Error Handling" label="Settings" />
            </div>

            <div className="space-y-3">
              {settings.map((setting) => (
                <div key={setting.key} className="glass-card p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="font-mono text-sm text-white">{setting.key}</p>
                    <button
                      onClick={() => saveSettingMutation.mutate({
                        key: setting.key,
                        value: settingsDrafts[setting.key] ?? setting.value,
                      })}
                      className="text-[10px] font-black uppercase tracking-widest text-cyan-300"
                    >
                      Save Setting
                    </button>
                  </div>
                  <input
                    type="text"
                    value={settingsDrafts[setting.key] ?? setting.value}
                    onChange={(event) => setSettingsDrafts((current) => ({ ...current, [setting.key]: event.target.value }))}
                    className="glass-input w-full"
                  />
                  {setting.description && (
                    <p className="mt-2 text-xs text-slate-500">{setting.description}</p>
                  )}
                </div>
              ))}
            </div>

            <div className="mt-6 grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_auto]">
              <input
                type="text"
                value={newSettingKey}
                onChange={(event) => setNewSettingKey(event.target.value)}
                placeholder="new_setting_key"
                className="glass-input"
              />
              <input
                type="text"
                value={newSettingValue}
                onChange={(event) => setNewSettingValue(event.target.value)}
                placeholder="value"
                className="glass-input"
              />
              <button
                onClick={() => {
                  if (!newSettingKey.trim()) {
                    toast.error('Enter a setting key');
                    return;
                  }
                  saveSettingMutation.mutate({ key: newSettingKey.trim(), value: newSettingValue });
                  setNewSettingKey('');
                  setNewSettingValue('');
                }}
                className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
              >
                <KeyRound size={14} />
                Add
              </button>
            </div>
          </div>

          <div className="glass-panel p-6">
            <h3 className="mb-6 text-xl font-bold text-white flex items-center gap-3">
              <Database size={20} className="text-purple-400" />
              Advanced Database Insights
            </h3>
            
            <div className="space-y-4">
                {ragStats?.breakdown && Object.entries(ragStats.breakdown).map(([name, stats]) => (
                  <button
                    key={name}
                    onClick={() => setInspectingCollection(name)}
                    className="w-full text-left glass-card p-4 transition hover:bg-white/5 active:scale-[0.98]"
                  >
                    <div className="flex items-center justify-between mb-3">
                       <p className="font-bold text-white uppercase tracking-tighter">{name.replace('_', ' ')}</p>
                       <span className="text-[10px] font-black uppercase tracking-widest text-indigo-300">View Data &rarr;</span>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                       <div className="bg-white/5 rounded-xl p-3 border border-white/5">
                          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">Chunks</p>
                          <p className="text-xl font-bold text-white">{stats.chunks.toLocaleString()}</p>
                       </div>
                       <div className="bg-white/5 rounded-xl p-3 border border-white/5">
                          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">Documents</p>
                          <p className="text-xl font-bold text-white">{stats.documents.toLocaleString()}</p>
                       </div>
                    </div>
                  </button>
                ))}

               <div className="p-4 rounded-2xl bg-indigo-500/5 border border-indigo-500/10 text-xs text-slate-400">
                  <p>
                    <strong className="text-indigo-300">Note:</strong> Home Assistant entities are indexed as 1 document per entity. 
                    If you have many smart devices, your document count will reflect the total number of unique entities discovered.
                  </p>
               </div>
            </div>
          </div>

          <div className="glass-panel p-6">
            <h3 className="mb-2 text-xl font-bold text-white flex items-center gap-3">
              <Database size={20} className="text-orange-300" />
              Audit Trail
            </h3>
            <p className="mb-6 text-sm text-slate-400">Live service log events, refreshed every 10 seconds.</p>
            <div className="space-y-3 max-h-[28rem] overflow-y-auto pr-1">
              {logs.length === 0 ? (
                <p className="text-center py-8 text-slate-500 text-sm italic">No log events yet. Services may still be starting.</p>
              ) : logs.map((log, index) => (
                <div key={`${log.timestamp}-${index}`} className="glass-card p-4 overflow-hidden">
                  <div className="flex items-center justify-between gap-4">
                    <p className="font-semibold text-white truncate text-sm">{log.service}</p>
                    <span className={`text-[9px] font-black uppercase tracking-widest shrink-0 ${
                      log.level === 'ERROR' ? 'text-red-400' :
                      log.level === 'WARNING' || log.level === 'WARN' ? 'text-yellow-400' :
                      log.level === 'INFO' ? 'text-emerald-400' : 'text-slate-400'
                    }`}>{log.level}</span>
                  </div>
                  <p className="mt-1.5 text-sm text-slate-300 break-words">{log.message}</p>
                  <p className="mt-1.5 text-xs text-slate-500">{new Date(log.timestamp).toLocaleString()}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      <Modal
        isOpen={isUserModalOpen}
        onClose={() => {
          setEditingUser(null);
          setUserForm(emptyUserForm);
          setIsUserModalOpen(false);
        }}
        title={editingUser ? `Edit @${editingUser.username}` : 'Create User'}
      >
        <div className="space-y-6">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-2">
              <span className="text-xs font-black uppercase tracking-widest text-slate-500">Username</span>
              <input
                type="text"
                value={userForm.username}
                aria-label="Username"
                disabled={Boolean(editingUser)}
                onChange={(event) => setUserForm((current) => ({ ...current, username: event.target.value }))}
                className="glass-input w-full disabled:opacity-50"
              />
            </label>
            <label className="space-y-2">
              <span className="text-xs font-black uppercase tracking-widest text-slate-500">Display Name</span>
              <input
                type="text"
                value={userForm.full_name}
                aria-label="Display Name"
                onChange={(event) => setUserForm((current) => ({ ...current, full_name: event.target.value }))}
                className="glass-input w-full"
              />
            </label>
          </div>

          <label className="flex items-center gap-3 rounded-2xl border border-white/5 bg-white/5 p-4">
            <input
              type="checkbox"
              checked={userForm.is_admin}
              onChange={(event) => setUserForm((current) => ({ ...current, is_admin: event.target.checked }))}
            />
            <span className="text-sm text-slate-300">Grant admin privileges</span>
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            {[
              ['Home Assistant URL', 'ha_url'],
              ['Home Assistant Token', 'ha_token'],
              ['Nextcloud URL', 'nextcloud_url'],
              ['Nextcloud Username', 'nextcloud_user'],
              ['Nextcloud Password', 'nextcloud_pass'],
              ['GitHub URL', 'github_url'],
              ['GitHub Username', 'github_user'],
              ['GitHub Token', 'github_token'],
              ['GitLab URL', 'gitlab_url'],
              ['GitLab Username', 'gitlab_user'],
              ['GitLab Token', 'gitlab_token'],
              ['Audiobookshelf URL', 'audiobookshelf_url'],
              ['Audiobookshelf Username', 'audiobookshelf_user'],
              ['Audiobookshelf Password', 'audiobookshelf_pass'],
            ].map(([label, key]) => (
              <label key={key} className="space-y-2">
                <span className="text-xs font-black uppercase tracking-widest text-slate-500">{label}</span>
                <input
                  type={label.toLowerCase().includes('token') || label.toLowerCase().includes('password') ? 'password' : 'text'}
                  value={userForm[key as keyof UserFormState] as string}
                  aria-label={label}
                  onChange={(event) => setUserForm((current) => ({
                    ...current,
                    [key]: event.target.value,
                  }))}
                  className="glass-input w-full"
                />
              </label>
            ))}
          </div>

          <div className="flex gap-3">
            <button
              onClick={() => {
                setEditingUser(null);
                setUserForm(emptyUserForm);
                setIsUserModalOpen(false);
              }}
              className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                if (!userForm.username.trim()) {
                  toast.error('Username is required');
                  return;
                }
                saveUserMutation.mutate(userForm);
              }}
              className="glass-button flex-1 px-4 py-3 bg-indigo-600/30 border-indigo-500/30 text-[10px] font-black uppercase tracking-widest text-indigo-300"
            >
              <Save size={14} />
              Save User
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={Boolean(passwordModalUser)}
        onClose={() => {
          setPasswordModalUser(null);
          setNewPassword('');
        }}
        title={`Set Password for @${passwordModalUser}`}
      >
        <div className="space-y-6">
          <p className="text-sm text-slate-400">
            Enter a new password for this user. They will be able to log in with this immediately.
          </p>
          <label className="block space-y-2">
            <span className="text-xs font-black uppercase tracking-widest text-slate-500">New Password</span>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="glass-input w-full"
              placeholder="••••••••"
              autoFocus
            />
          </label>
          <div className="flex gap-3">
             <button
              onClick={() => setPasswordModalUser(null)}
              className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                if (!newPassword) {
                  toast.error('Enter a password');
                  return;
                }
                setPasswordMutation.mutate({ username: passwordModalUser!, password: newPassword });
              }}
              disabled={setPasswordMutation.isPending}
              className="glass-button flex-1 px-4 py-3 bg-indigo-600/30 border-indigo-500/30 text-[10px] font-black uppercase tracking-widest text-indigo-300"
            >
              {setPasswordMutation.isPending ? 'Updating...' : 'Set Password'}
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={Boolean(inspectingCollection)}
        onClose={() => setInspectingCollection(null)}
        title={`Inspect: ${inspectingCollection?.replace('_', ' ')}`}
      >
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-400">
              Displaying the latest {inspectLimit} documents from the vector database.
            </p>
            <select
              value={inspectLimit}
              onChange={(e) => setInspectLimit(Number(e.target.value))}
              className="glass-input text-xs"
            >
              <option value={20}>20 rows</option>
              <option value={50}>50 rows</option>
              <option value={100}>100 rows</option>
            </select>
          </div>

          {isFetchingDocs ? (
            <div className="flex h-64 items-center justify-center">
              <RefreshCcw className="animate-spin text-indigo-400" size={32} />
            </div>
          ) : (
            <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-2 custom-scrollbar">
              {collectionDocs?.items?.map((item: any) => (
                <div key={item.id} className="rounded-2xl border border-white/5 bg-black/40 p-4 font-mono text-[11px]">
                  <div className="mb-2 flex items-center justify-between border-b border-white/5 pb-2">
                    <span className="text-indigo-300">ID: {item.id}</span>
                    <span className="text-slate-500 uppercase tracking-widest">{item.metadata?.type || 'Record'}</span>
                  </div>
                  <p className="mb-3 text-slate-200 whitespace-pre-wrap leading-relaxed">
                    {item.document}
                  </p>
                  <div className="grid grid-cols-2 gap-2 text-[10px] text-slate-500">
                    {Object.entries(item.metadata || {}).map(([k, v]) => (
                      <div key={k} className="truncate">
                        <span className="text-slate-600">{k}:</span> {String(v)}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {!collectionDocs?.items?.length && (
                <p className="py-12 text-center text-slate-500">No documents found in this collection.</p>
              )}
            </div>
          )}

          <div className="flex justify-end">
            <button
              onClick={() => setInspectingCollection(null)}
              className="glass-button px-6 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              Close Insights
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default Admin;

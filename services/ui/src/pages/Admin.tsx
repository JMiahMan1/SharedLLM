import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
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
  Globe,
  ShieldAlert,
  Code2,
  BarChart3,
  Layers,
  Lightbulb,
  Play,
  Plus,
  Activity,
  TrendingUp,
  Radio,
  Megaphone,
  Phone,
  Server,
  Power,
  ArrowUpCircle,
  PowerOff,
  RefreshCw,
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
import EntitySearchDropdown from '../components/ui/EntitySearchDropdown';
import EntityMultiSelect from '../components/ui/EntityMultiSelect';
import LLMSettings from '../components/settings/LLMSettings';
import RavenOpsPanel from '../components/settings/RavenOpsPanel';
import DnsManagementPanel from '../components/settings/DnsManagementPanel';

type AdminTab = 'users' | 'groups' | 'telemetry' | 'intercom' | 'raven' | 'settings' | 'database' | 'services';

const tabs: { id: AdminTab; label: string; icon: React.ElementType; path: string }[] = [
  { id: 'users', label: 'Users & Devices', icon: Shield, path: '/admin/users' },
  { id: 'groups', label: 'Device Groups', icon: Layers, path: '/admin/groups' },
  { id: 'telemetry', label: 'Telemetry', icon: Activity, path: '/admin/monitor' },
  { id: 'intercom', label: 'Intercom', icon: Phone, path: '/admin/intercom' },
  { id: 'raven', label: 'Raven Ops', icon: ShieldAlert, path: '/admin/ops' },
  { id: 'settings', label: 'LLM & Settings', icon: Code2, path: '/admin/integrations' },
  { id: 'database', label: 'Database & Audit', icon: BarChart3, path: '/admin/database' },
  { id: 'services', label: 'System Services', icon: Server, path: '/admin/services' },
];

const adminTabFromPathname = (pathname: string): AdminTab => {
  if (pathname.startsWith('/admin/ops')) return 'raven';
  if (pathname.startsWith('/admin/groups')) return 'groups';
  if (pathname.startsWith('/admin/monitor')) return 'telemetry';
  if (pathname.startsWith('/admin/integrations')) return 'settings';
  if (pathname.startsWith('/admin/sounds')) return 'database';
  if (pathname.startsWith('/admin/database')) return 'database';
  if (pathname.startsWith('/admin/intercom')) return 'intercom';
  if (pathname.startsWith('/admin/users')) return 'users';
  if (pathname.startsWith('/admin/services')) return 'services';
  return 'users';
};

type UserFormState = {
  username: string;
  full_name: string;
  is_admin: boolean;
  password: string;
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
  audiobookshelf_api_key: string;
};

const emptyUserForm: UserFormState = {
  username: '',
  full_name: '',
  is_admin: false,
  password: '',
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
  audiobookshelf_api_key: '',
};

const toUserForm = (user?: UserProfile | null): UserFormState => ({
  username: user?.username ?? '',
  full_name: user?.full_name ?? '',
  is_admin: Boolean(user?.is_admin),
  password: '',
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
  audiobookshelf_api_key: String(user?.audiobookshelf_api_key ?? ''),
});

const Admin = () => {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab = adminTabFromPathname(location.pathname);
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
  const [groupTab, setGroupTab] = useState<'media' | 'lights' | 'patterns'>('media');
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupMembers, setNewGroupMembers] = useState('');
  const [newPatternName, setNewPatternName] = useState('');
  const [newPatternSteps, setNewPatternSteps] = useState('');
  const [executePatternName, setExecutePatternName] = useState('');
  const [executeTargetCluster, setExecuteTargetCluster] = useState('');
  const [telemetryEntityId, setTelemetryEntityId] = useState('');
  const [telemetryOfflineThreshold, setTelemetryOfflineThreshold] = useState(30);
  const [intercomTab, setIntercomTab] = useState<'sessions' | 'broadcast' | 'announce' | 'config'>('sessions');
  const [broadcastMessage, setBroadcastMessage] = useState('');
  const [broadcastTargets, setBroadcastTargets] = useState('');
  const [announceMessage, setAnnounceMessage] = useState('');
  const [announceTargets, setAnnounceTargets] = useState('');
  const [intercomSessionTarget, setIntercomSessionTarget] = useState('');
  const [intercomSessionType, setIntercomSessionType] = useState<'twoway' | 'broadcast' | 'announcement'>('twoway');

  const { data: users = [] } = useQuery<UserProfile[]>({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
  });

  const { data: discoveryData, refetch: refetchDiscoveredUsers, isFetching: isDiscovering } = useQuery<{ users: DiscoveredUser[]; warnings: string[]; errors: string[] }>({
    queryKey: ['discovered-users'],
    queryFn: () => api.discoverUsers(),
  });
  const discoveredUsers = useMemo(() => discoveryData?.users ?? [], [discoveryData]);
  const discoveryWarnings = discoveryData?.warnings ?? [];
  const discoveryErrors = discoveryData?.errors ?? [];

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

  const { data: systemHealth, isFetching: isFetchingHealth, isError: healthError, error: healthErrorData } = useQuery({
    queryKey: ['system-health'],
    queryFn: () => api.getSystemHealth(),
    refetchInterval: 10000,
  });

  const { data: updatesData } = useQuery({
    queryKey: ['service-updates'],
    queryFn: () => api.checkAllUpdates(),
    refetchInterval: 60000,
    refetchOnWindowFocus: true,
  });

  const pullImageMutation = useMutation({
    mutationFn: (serviceName: string) => api.pullServiceImage(serviceName),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['system-health'] });
      queryClient.invalidateQueries({ queryKey: ['service-updates'] });
      toast.success(data.message || 'Image pull completed');
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'Image pull failed');
    },
  });

  const restartServiceMutation = useMutation({
    mutationFn: (serviceName: string) => api.restartService(serviceName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['system-health'] });
      toast.success('Service restart initiated');
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'Restart failed');
    },
  });

  const saveUserMutation = useMutation({
    mutationFn: async (form: UserFormState) => {
      const payload = {
        username: form.username.trim().toLowerCase(),
        full_name: form.full_name,
        is_admin: form.is_admin,
        password: form.password || undefined,
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
        audiobookshelf_api_key: form.audiobookshelf_api_key,
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
    onSuccess: (data: { message?: string }) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success(data.message || 'Users imported from Nextcloud');
    },
    onError: (error: Error) => toast.error(error.message || 'Nextcloud import failed'),
  });

  interface GroupItem { name: string; member_entity_ids?: string[]; steps?: unknown[] }
  interface IntercomSessionItem { session_id: string; caller_user_id: string; target_user_id?: string; target_room?: string; session_type: string; status: string }

  const { data: mediaGroups = [] } = useQuery<GroupItem[]>({
    queryKey: ['media-groups'],
    queryFn: () => api.getMediaGroups(),
  });

  const { data: lightClusters = [] } = useQuery<GroupItem[]>({
    queryKey: ['light-clusters'],
    queryFn: () => api.getLightClusters(),
  });

  const { data: lightPatterns = [] } = useQuery<GroupItem[]>({
    queryKey: ['light-patterns'],
    queryFn: () => api.getLightPatterns(),
  });

  const createMediaGroupMutation = useMutation({
    mutationFn: (data: { name: string; member_entity_ids: string[] }) => api.createMediaGroup(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-groups'] });
      setNewGroupName('');
      setNewGroupMembers('');
      toast.success('Media group created');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to create media group'),
  });

  const deleteMediaGroupMutation = useMutation({
    mutationFn: (name: string) => api.deleteMediaGroup(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-groups'] });
      toast.success('Media group deleted');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete media group'),
  });

  const createLightClusterMutation = useMutation({
    mutationFn: (data: { name: string; member_entity_ids: string[] }) => api.createLightCluster(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['light-clusters'] });
      setNewGroupName('');
      setNewGroupMembers('');
      toast.success('Light cluster created');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to create light cluster'),
  });

  const deleteLightClusterMutation = useMutation({
    mutationFn: (name: string) => api.deleteLightCluster(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['light-clusters'] });
      toast.success('Light cluster deleted');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete light cluster'),
  });

  interface PatternStep { brightness?: number; color_temp?: number; rgb_color?: number[]; transition?: number; delay?: number }
  const createLightPatternMutation = useMutation({
    mutationFn: (data: { name: string; steps: PatternStep[] }) => api.createLightPattern(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['light-patterns'] });
      setNewPatternName('');
      setNewPatternSteps('');
      toast.success('Light pattern created');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to create light pattern'),
  });

  const deleteLightPatternMutation = useMutation({
    mutationFn: (name: string) => api.deleteLightPattern(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['light-patterns'] });
      toast.success('Light pattern deleted');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete light pattern'),
  });

  const executePatternMutation = useMutation({
    mutationFn: (data: { pattern_name: string; target_cluster?: string }) => api.executeLightPattern(data),
    onSuccess: () => {
      toast.success('Pattern execution started');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to execute pattern'),
  });

  interface TelemetryEnrollmentItem { entity_id: string; power_tracking: boolean; availability_tracking: boolean; offline_alert_threshold_minutes: number }
  const { data: telemetryEnrollments = [] } = useQuery<TelemetryEnrollmentItem[]>({
    queryKey: ['telemetry-enrollments'],
    queryFn: () => api.getTelemetryEnrollments(),
  });

  const enrollTelemetryMutation = useMutation({
    mutationFn: (data: { entity_id: string; offline_alert_threshold_minutes: number }) => api.enrollTelemetry(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telemetry-enrollments'] });
      setTelemetryEntityId('');
      toast.success('Device enrolled in telemetry');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to enroll device'),
  });

  const unenrollTelemetryMutation = useMutation({
    mutationFn: (entity_id: string) => api.unenrollTelemetry(entity_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telemetry-enrollments'] });
      toast.success('Device unenrolled from telemetry');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to unenroll device'),
  });

  const analyzeTelemetryMutation = useMutation({
    mutationFn: () => api.analyzeTelemetry(),
    onSuccess: () => {
      toast.success('Telemetry analysis queued');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to trigger analysis'),
  });

  const { data: intercomSessions = [] } = useQuery<IntercomSessionItem[]>({
    queryKey: ['intercom-sessions'],
    queryFn: () => api.getIntercomSessions(),
  });

  interface IntercomConfig { default_tts_engine?: string; default_voice?: string; default_volume?: number; enable_espresense_routing?: boolean }
  const { data: intercomConfig } = useQuery<IntercomConfig>({
    queryKey: ['intercom-config'],
    queryFn: () => api.getIntercomConfig(),
  });

  const startIntercomMutation = useMutation({
    mutationFn: (data: { target_user_id?: string; target_room?: string; target_entity_ids?: string[]; session_type?: string }) =>
      api.startIntercomSession(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['intercom-sessions'] });
      toast.success('Intercom session started');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to start intercom'),
  });

  const endIntercomMutation = useMutation({
    mutationFn: (session_id: string) => api.endIntercomSession(session_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['intercom-sessions'] });
      toast.success('Intercom session ended');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to end intercom'),
  });

  const broadcastMutation = useMutation({
    mutationFn: (data: { message: string; target_entity_ids: string[] }) => api.intercomBroadcast(data),
    onSuccess: () => {
      setBroadcastMessage('');
      setBroadcastTargets('');
      toast.success('Broadcast sent');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to send broadcast'),
  });

  const announceMutation = useMutation({
    mutationFn: (data: { message: string; target_devices: string[] }) => api.intercomAnnounce(data),
    onSuccess: () => {
      setAnnounceMessage('');
      setAnnounceTargets('');
      toast.success('Announcement sent');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to send announcement'),
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
    <div className="space-y-6 pb-12">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tighter text-white uppercase">System Matrix</h2>
          <p className="mt-2 text-slate-400">Admin-only controls for users, Raven, settings, and database.</p>
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

      {/* Tab Navigation */}
      <div className="flex gap-2 border-b border-white/10 pb-2 overflow-x-auto">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => navigate(tab.path)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase tracking-widest transition whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-purple-600/30 text-white border border-purple-500/30'
                  : 'text-slate-400 hover:text-white hover:bg-white/5'
              }`}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      {activeTab === 'users' && (
        <div className="space-y-8">
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

              {discoveryWarnings.length > 0 && (
                <div className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/10 px-4 py-3">
                  <p className="text-sm font-semibold text-amber-300">Discovery Warnings</p>
                  <ul className="mt-1 space-y-1">
                    {discoveryWarnings.map((w, i) => (
                      <li key={i} className="text-xs text-amber-200/80">• {w}</li>
                    ))}
                  </ul>
                </div>
              )}

              {discoveryErrors.length > 0 && (
                <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3">
                  <p className="text-sm font-semibold text-red-300">Discovery Errors</p>
                  <ul className="mt-1 space-y-1">
                    {discoveryErrors.map((e, i) => (
                      <li key={i} className="text-xs text-red-200/80">• {e}</li>
                    ))}
                  </ul>
                </div>
              )}

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
              <EntitySearchDropdown
                value={deviceId}
                onChange={setDeviceId}
                placeholder="Search Home Assistant entities..."
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
        </div>
      )}

      {activeTab === 'groups' && (
        <div className="space-y-6">
          <div className="flex gap-2 border-b border-white/10 pb-2">
            {(['media', 'lights', 'patterns'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setGroupTab(tab)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase tracking-widest transition ${
                  groupTab === tab
                    ? 'bg-emerald-600/30 text-white border border-emerald-500/30'
                    : 'text-slate-400 hover:text-white hover:bg-white/5'
                }`}
              >
                {tab === 'media' ? <Cpu size={14} /> : tab === 'lights' ? <Lightbulb size={14} /> : <Play size={14} />}
                {tab === 'media' ? 'Media Groups' : tab === 'lights' ? 'Light Clusters' : 'Light Patterns'}
              </button>
            ))}
          </div>

          {groupTab === 'media' && (
            <div className="space-y-6">
              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Plus size={20} className="text-emerald-400" />
                  Create Media Group
                </h3>
                <div className="grid gap-3 grid-cols-1 sm:grid-cols-[1fr_2fr_auto]">
                  <input
                    type="text"
                    value={newGroupName}
                    onChange={(e) => setNewGroupName(e.target.value)}
                    placeholder="Group name (e.g., Living Room TVs)"
                    className="glass-input"
                  />
                  <EntityMultiSelect
                    values={newGroupMembers.split(',').map((s) => s.trim()).filter(Boolean)}
                    onChange={(vals) => setNewGroupMembers(vals.join(', '))}
                    placeholder="Search and add media entities..."
                    domainFilter="media_player"
                  />
                  <button
                    onClick={() => {
                      if (!newGroupName.trim() || !newGroupMembers.trim()) {
                        toast.error('Enter a name and member entity IDs');
                        return;
                      }
                      createMediaGroupMutation.mutate({
                        name: newGroupName.trim(),
                        member_entity_ids: newGroupMembers.split(',').map((s) => s.trim()).filter(Boolean),
                      });
                    }}
                    disabled={createMediaGroupMutation.isPending}
                    className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
                  >
                    <Save size={14} />
                    Create
                  </button>
                </div>
              </section>

              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Cpu size={20} className="text-orange-300" />
                  Media Groups
                </h3>
                <div className="space-y-3">
                  {mediaGroups.map((group) => (
                    <div key={group.name} className="glass-card flex items-center justify-between p-4">
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-white">{group.name}</p>
                        <p className="mt-1 text-xs text-slate-400 truncate">
                          {group.member_entity_ids?.join(', ') || 'No members'}
                        </p>
                      </div>
                      <button
                        onClick={() => deleteMediaGroupMutation.mutate(group.name)}
                        className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                        aria-label={`Delete ${group.name}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                  {!mediaGroups.length && (
                    <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                      No media groups configured.
                    </p>
                  )}
                </div>
              </section>
            </div>
          )}

          {groupTab === 'lights' && (
            <div className="space-y-6">
              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Plus size={20} className="text-yellow-400" />
                  Create Light Cluster
                </h3>
                <div className="grid gap-3 grid-cols-1 sm:grid-cols-[1fr_2fr_auto]">
                  <input
                    type="text"
                    value={newGroupName}
                    onChange={(e) => setNewGroupName(e.target.value)}
                    placeholder="Cluster name (e.g., Kitchen Lights)"
                    className="glass-input"
                  />
                  <EntityMultiSelect
                    values={newGroupMembers.split(',').map((s) => s.trim()).filter(Boolean)}
                    onChange={(vals) => setNewGroupMembers(vals.join(', '))}
                    placeholder="Search and add light entities..."
                    domainFilter="light"
                  />
                  <button
                    onClick={() => {
                      if (!newGroupName.trim() || !newGroupMembers.trim()) {
                        toast.error('Enter a name and member entity IDs');
                        return;
                      }
                      createLightClusterMutation.mutate({
                        name: newGroupName.trim(),
                        member_entity_ids: newGroupMembers.split(',').map((s) => s.trim()).filter(Boolean),
                      });
                    }}
                    disabled={createLightClusterMutation.isPending}
                    className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
                  >
                    <Save size={14} />
                    Create
                  </button>
                </div>
              </section>

              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Lightbulb size={20} className="text-yellow-300" />
                  Light Clusters
                </h3>
                <div className="space-y-3">
                  {lightClusters.map((cluster) => (
                    <div key={cluster.name} className="glass-card flex items-center justify-between p-4">
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-white">{cluster.name}</p>
                        <p className="mt-1 text-xs text-slate-400 truncate">
                          {cluster.member_entity_ids?.join(', ') || 'No members'}
                        </p>
                      </div>
                      <button
                        onClick={() => deleteLightClusterMutation.mutate(cluster.name)}
                        className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                        aria-label={`Delete ${cluster.name}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                  {!lightClusters.length && (
                    <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                      No light clusters configured.
                    </p>
                  )}
                </div>
              </section>
            </div>
          )}

          {groupTab === 'patterns' && (
            <div className="space-y-6">
              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Plus size={20} className="text-purple-400" />
                  Create Light Pattern
                </h3>
                <div className="space-y-3">
                  <input
                    type="text"
                    value={newPatternName}
                    onChange={(e) => setNewPatternName(e.target.value)}
                    placeholder="Pattern name (e.g., Movie Night)"
                    className="glass-input w-full"
                  />
                  <textarea
                    value={newPatternSteps}
                    onChange={(e) => setNewPatternSteps(e.target.value)}
                    placeholder='JSON steps: [{"brightness": 255, "color_temp": 300, "transition": 5}, {"brightness": 50, "delay": 30}]'
                    className="glass-input w-full min-h-[100px] font-mono text-xs"
                  />
                  <button
                    onClick={() => {
                      if (!newPatternName.trim() || !newPatternSteps.trim()) {
                        toast.error('Enter a name and pattern steps (JSON)');
                        return;
                      }
                      try {
                        const steps = JSON.parse(newPatternSteps);
                        if (!Array.isArray(steps)) {
                          toast.error('Steps must be a JSON array');
                          return;
                        }
                        createLightPatternMutation.mutate({
                          name: newPatternName.trim(),
                          steps,
                        });
                      } catch {
                        toast.error('Invalid JSON in steps');
                      }
                    }}
                    disabled={createLightPatternMutation.isPending}
                    className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
                  >
                    <Save size={14} />
                    Create Pattern
                  </button>
                </div>
              </section>

              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Play size={20} className="text-cyan-400" />
                  Execute Pattern
                </h3>
                <div className="grid gap-3 grid-cols-1 sm:grid-cols-[1fr_1fr_auto]">
                  <select
                    value={executePatternName}
                    onChange={(e) => setExecutePatternName(e.target.value)}
                    className="glass-input bg-black/30"
                  >
                    <option value="">Select pattern</option>
                    {lightPatterns.map((p) => (
                      <option key={p.name} value={p.name}>{p.name}</option>
                    ))}
                  </select>
                  <select
                    value={executeTargetCluster}
                    onChange={(e) => setExecuteTargetCluster(e.target.value)}
                    className="glass-input bg-black/30"
                  >
                    <option value="">Target cluster (optional)</option>
                    {lightClusters.map((c) => (
                      <option key={c.name} value={c.name}>{c.name}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => {
                      if (!executePatternName) {
                        toast.error('Select a pattern to execute');
                        return;
                      }
                      executePatternMutation.mutate({
                        pattern_name: executePatternName,
                        target_cluster: executeTargetCluster || undefined,
                      });
                    }}
                    disabled={executePatternMutation.isPending}
                    className="glass-button px-4 py-3 bg-cyan-600/30 border-cyan-500/30 text-[10px] font-black uppercase tracking-widest text-cyan-300"
                  >
                    <Play size={14} />
                    Execute
                  </button>
                </div>
              </section>

              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Lightbulb size={20} className="text-purple-300" />
                  Light Patterns
                </h3>
                <div className="space-y-3">
                  {lightPatterns.map((pattern) => (
                    <div key={pattern.name} className="glass-card flex items-center justify-between p-4">
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-white">{pattern.name}</p>
                        <p className="mt-1 text-xs text-slate-400">
                          {pattern.steps?.length || 0} step(s)
                        </p>
                      </div>
                      <button
                        onClick={() => deleteLightPatternMutation.mutate(pattern.name)}
                        className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                        aria-label={`Delete ${pattern.name}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                  {!lightPatterns.length && (
                    <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                      No light patterns configured. System defaults will be seeded on first use.
                    </p>
                  )}
                </div>
              </section>
            </div>
          )}
        </div>
      )}

      {activeTab === 'telemetry' && (
        <div className="space-y-6">
          <section className="glass-panel p-6">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h3 className="flex items-center gap-3 text-xl font-bold text-white">
                  <Activity size={20} className="text-cyan-400" />
                  Device Telemetry Monitoring
                </h3>
                <p className="mt-1 text-sm text-slate-400">Enroll devices for power, availability, and usage tracking.</p>
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

            <div className="mb-4 grid gap-3 grid-cols-1 sm:grid-cols-[1fr_160px_auto]">
              <EntitySearchDropdown
                value={telemetryEntityId}
                onChange={setTelemetryEntityId}
                placeholder="Search HA entities for telemetry..."
              />
              <input
                type="number"
                value={telemetryOfflineThreshold}
                onChange={(e) => setTelemetryOfflineThreshold(Number(e.target.value))}
                placeholder="Offline threshold (min)"
                className="glass-input"
              />
              <button
                onClick={() => {
                  if (!telemetryEntityId.trim()) {
                    toast.error('Enter an entity ID');
                    return;
                  }
                  enrollTelemetryMutation.mutate({
                    entity_id: telemetryEntityId.trim(),
                    offline_alert_threshold_minutes: telemetryOfflineThreshold,
                    power_tracking: true,
                    type: 'energy',
                  });
                }}
                disabled={enrollTelemetryMutation.isPending}
                className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
              >
                <Plus size={14} />
                Enroll
              </button>
            </div>
          </section>

          <section className="glass-panel p-6">
            <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
              <TrendingUp size={20} className="text-emerald-400" />
              Enrolled Devices
            </h3>
            <div className="space-y-3">
              {telemetryEnrollments.map((enrollment) => (
                <div key={enrollment.entity_id} className="glass-card flex items-center justify-between p-4">
                  <div className="min-w-0 flex-1">
                    <p className="font-semibold text-white truncate">{enrollment.entity_id}</p>
                    <p className="mt-1 text-xs text-slate-400">
                      Power: {enrollment.power_tracking ? 'Yes' : 'No'} | Availability: {enrollment.availability_tracking ? 'Yes' : 'No'} | Alert after: {enrollment.offline_alert_threshold_minutes}min
                    </p>
                  </div>
                  <button
                    onClick={() => unenrollTelemetryMutation.mutate(enrollment.entity_id)}
                    className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                    aria-label={`Unenroll ${enrollment.entity_id}`}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
              {!telemetryEnrollments.length && (
                <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                  No devices enrolled in telemetry monitoring.
                </p>
              )}
            </div>
          </section>
        </div>
      )}

      {activeTab === 'intercom' && (
        <div className="space-y-6">
          <div className="flex gap-2 border-b border-white/10 pb-2">
            {(['sessions', 'broadcast', 'announce', 'config'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setIntercomTab(tab)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase tracking-widest transition ${
                  intercomTab === tab
                    ? 'bg-violet-600/30 text-white border border-violet-500/30'
                    : 'text-slate-400 hover:text-white hover:bg-white/5'
                }`}
              >
                {tab === 'sessions' ? <Phone size={14} /> : tab === 'broadcast' ? <Radio size={14} /> : tab === 'announce' ? <Megaphone size={14} /> : <Settings2 size={14} />}
                {tab === 'sessions' ? 'Sessions' : tab === 'broadcast' ? 'Broadcast' : tab === 'announce' ? 'Announce' : 'Config'}
              </button>
            ))}
          </div>

          {intercomTab === 'sessions' && (
            <div className="space-y-6">
              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Phone size={20} className="text-violet-400" />
                  Start Intercom Session
                </h3>
                <div className="mb-4 grid gap-3 grid-cols-1 sm:grid-cols-[1fr_160px_auto]">
                  <input
                    type="text"
                    value={intercomSessionTarget}
                    onChange={(e) => setIntercomSessionTarget(e.target.value)}
                    placeholder="Target user ID or room name"
                    className="glass-input"
                  />
                  <select
                    value={intercomSessionType}
                    onChange={(e) => setIntercomSessionType(e.target.value as 'twoway' | 'broadcast' | 'announcement')}
                    className="glass-input bg-black/30"
                  >
                    <option value="twoway">Two-Way</option>
                    <option value="broadcast">Broadcast</option>
                    <option value="announcement">Announcement</option>
                  </select>
                  <button
                    onClick={() => {
                      if (!intercomSessionTarget.trim()) {
                        toast.error('Enter a target user or room');
                        return;
                      }
                      startIntercomMutation.mutate({
                        target_user_id: intercomSessionType === 'twoway' ? intercomSessionTarget.trim() : undefined,
                        target_room: intercomSessionType !== 'twoway' ? intercomSessionTarget.trim() : undefined,
                        session_type: intercomSessionType,
                      });
                    }}
                    disabled={startIntercomMutation.isPending}
                    className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
                  >
                    <Phone size={14} />
                    Start
                  </button>
                </div>
              </section>

              <section className="glass-panel p-6">
                <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                  <Phone size={20} className="text-violet-400" />
                  Active Intercom Sessions
                </h3>
                <div className="space-y-3">
                  {intercomSessions.filter((s: IntercomSessionItem) => s.status === 'active').map((session: IntercomSessionItem) => (
                    <div key={session.session_id} className="glass-card flex items-center justify-between p-4">
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-white">Session: {session.session_id}</p>
                        <p className="mt-1 text-xs text-slate-400">
                          Caller: {session.caller_user_id} | Target: {session.target_user_id || session.target_room || 'All'} | Type: {session.session_type}
                        </p>
                      </div>
                      <button
                        onClick={() => endIntercomMutation.mutate(session.session_id)}
                        className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                        aria-label={`End session ${session.session_id}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                  {!intercomSessions.filter((s: IntercomSessionItem) => s.status === 'active').length && (
                    <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                      No active intercom sessions.
                    </p>
                  )}
                </div>
              </section>
            </div>
          )}

          {intercomTab === 'broadcast' && (
            <section className="glass-panel p-6">
              <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                <Radio size={20} className="text-amber-400" />
                Broadcast Message
              </h3>
              <div className="space-y-3">
                <textarea
                  value={broadcastMessage}
                  onChange={(e) => setBroadcastMessage(e.target.value)}
                  placeholder="Message to broadcast..."
                  className="glass-input w-full min-h-[80px]"
                />
                <EntityMultiSelect
                  values={broadcastTargets.split(',').map((s) => s.trim()).filter(Boolean)}
                  onChange={(vals) => setBroadcastTargets(vals.join(', '))}
                  placeholder="Search and add target entities..."
                />
                <button
                  onClick={() => {
                    if (!broadcastMessage.trim()) {
                      toast.error('Enter a message to broadcast');
                      return;
                    }
                    broadcastMutation.mutate({
                      message: broadcastMessage.trim(),
                      target_entity_ids: broadcastTargets.split(',').map((s) => s.trim()).filter(Boolean),
                    });
                  }}
                  disabled={broadcastMutation.isPending}
                  className="glass-button px-4 py-3 bg-amber-600/30 border-amber-500/30 text-[10px] font-black uppercase tracking-widest text-amber-300"
                >
                  <Radio size={14} />
                  Broadcast
                </button>
              </div>
            </section>
          )}

          {intercomTab === 'announce' && (
            <section className="glass-panel p-6">
              <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                <Megaphone size={20} className="text-cyan-400" />
                TV / Speaker Announcement
              </h3>
              <div className="space-y-3">
                <textarea
                  value={announceMessage}
                  onChange={(e) => setAnnounceMessage(e.target.value)}
                  placeholder="Announcement message..."
                  className="glass-input w-full min-h-[80px]"
                />
                <EntityMultiSelect
                  values={announceTargets.split(',').map((s) => s.trim()).filter(Boolean)}
                  onChange={(vals) => setAnnounceTargets(vals.join(', '))}
                  placeholder="Search and add target devices..."
                  domainFilter="media_player"
                />
                <button
                  onClick={() => {
                    if (!announceMessage.trim()) {
                      toast.error('Enter an announcement message');
                      return;
                    }
                    announceMutation.mutate({
                      message: announceMessage.trim(),
                      target_devices: announceTargets.split(',').map((s) => s.trim()).filter(Boolean),
                    });
                  }}
                  disabled={announceMutation.isPending}
                  className="glass-button px-4 py-3 bg-cyan-600/30 border-cyan-500/30 text-[10px] font-black uppercase tracking-widest text-cyan-300"
                >
                  <Megaphone size={14} />
                  Announce
                </button>
              </div>
            </section>
          )}

          {intercomTab === 'config' && (
            <section className="glass-panel p-6">
              <h3 className="mb-4 flex items-center gap-3 text-xl font-bold text-white">
                <Settings2 size={20} className="text-slate-400" />
                Intercom Configuration
              </h3>
              {intercomConfig ? (
                <div className="space-y-3">
                  <div className="glass-card p-4">
                    <p className="text-xs text-slate-400">Default TTS Engine</p>
                    <p className="font-mono text-sm text-white">{intercomConfig.default_tts_engine || 'kokoro'}</p>
                  </div>
                  <div className="glass-card p-4">
                    <p className="text-xs text-slate-400">Default Voice</p>
                    <p className="font-mono text-sm text-white">{intercomConfig.default_voice || 'af_heart'}</p>
                  </div>
                  <div className="glass-card p-4">
                    <p className="text-xs text-slate-400">Default Volume</p>
                    <p className="font-mono text-sm text-white">{intercomConfig.default_volume ?? 0.8}</p>
                  </div>
                  <div className="glass-card p-4">
                    <p className="text-xs text-slate-400">ESPresense Routing</p>
                    <p className="font-mono text-sm text-white">{intercomConfig.enable_espresense_routing !== false ? 'Enabled' : 'Disabled'}</p>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-slate-500">Loading configuration...</p>
              )}
            </section>
          )}
        </div>
      )}

      {activeTab === 'raven' && (
        <section className="glass-panel p-8 border-red-500/20">
          <RavenOpsPanel />
        </section>
      )}

      {activeTab === 'settings' && (
        <section className="glass-panel p-8 border-purple-500/20 space-y-8">
          <div className="glass-panel p-6 border border-white/10">
            <h3 className="flex items-center gap-3 text-xl font-bold text-white mb-6">
              <Globe size={20} className="text-blue-400" />
              DNS Management
            </h3>
            <DnsManagementPanel />
          </div>
          <div className="glass-panel p-6 border border-white/10">
            <h3 className="flex items-center gap-3 text-xl font-bold text-white mb-6">
              <Code2 size={20} className="text-purple-400" />
              LLM Configuration
            </h3>
            <LLMSettings />
          </div>
        </section>
      )}

      {activeTab === 'database' && (
        <div className="space-y-8">
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
              {settings
                .filter((s) => !['assistant_model', 'coding_model', 'librarian_model'].includes(s.key))
                .map((setting) => (
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
                {ragStats?.status === "ERROR" && (
                  <div className="p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-xs text-red-400">
                    <p className="font-bold mb-1 uppercase tracking-widest">Database Sync Error</p>
                    <p>{ragStats.message || "Failed to fetch database statistics."}</p>
                  </div>
                )}
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
        </div>
      )}

      {activeTab === 'services' && (
        <section className="space-y-6">
          <div className="glass-panel p-6">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h3 className="flex items-center gap-3 text-xl font-bold text-white">
                  <Server size={20} className="text-emerald-400" />
                  System Services Health
                </h3>
                <p className="mt-1 text-sm text-slate-400">Docker container status, image updates, and uptime metrics.</p>
              </div>
              <button
                onClick={() => queryClient.invalidateQueries({ queryKey: ['system-health'] })}
                disabled={isFetchingHealth}
                className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
              >
                <RefreshCcw size={14} className={isFetchingHealth ? 'animate-spin' : ''} />
                Refresh
              </button>
            </div>

            {isFetchingHealth && !systemHealth ? (
              <div className="flex h-48 items-center justify-center">
                <RefreshCcw className="animate-spin text-emerald-400" size={32} />
              </div>
            ) : healthError ? (
              <div className="flex flex-col items-center justify-center gap-3 py-12">
                <ShieldAlert size={32} className="text-red-400" />
                <p className="text-sm font-semibold text-red-400">Failed to load system health</p>
                {healthErrorData && (
                  <p className="max-w-md text-center text-xs text-slate-500">
                    {(healthErrorData as { response?: { status?: number; data?: { detail?: string } } })?.response?.status === 403
                      ? 'Admin access required. Please verify your account has admin privileges.'
                      : (healthErrorData as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Control plane may be unreachable.'}
                  </p>
                )}
                <button
                  onClick={() => queryClient.invalidateQueries({ queryKey: ['system-health'] })}
                  className="mt-2 glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest"
                >
                  <RefreshCcw size={12} className="inline mr-1" /> Retry
                </button>
              </div>
            ) : systemHealth ? (
              <>
                <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3">
                  <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                    <p className="text-[10px] font-black uppercase tracking-widest text-emerald-600">Running</p>
                    <p className="mt-1 text-2xl font-bold text-emerald-400">{systemHealth.running}</p>
                  </div>
                  <div className="rounded-2xl border border-slate-500/20 bg-slate-500/5 p-4">
                    <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Stopped</p>
                    <p className="mt-1 text-2xl font-bold text-slate-400">{systemHealth.stopped}</p>
                  </div>
                  <div className="rounded-2xl border border-orange-500/20 bg-orange-500/5 p-4">
                    <p className="text-[10px] font-black uppercase tracking-widest text-orange-600">Updates</p>
                    <p className="mt-1 text-2xl font-bold text-orange-400">{updatesData?.updates_available || 0}</p>
                  </div>
                </div>

                <div className="mb-6 rounded-2xl border border-indigo-500/20 bg-indigo-500/5 p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs font-black uppercase tracking-widest text-indigo-400">Control Plane</p>
                      <p className="mt-1 text-sm text-white font-mono">{systemHealth.control_plane.git_sha}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs font-black uppercase tracking-widest text-indigo-400">Uptime</p>
                      <p className="mt-1 text-lg font-bold text-white">{systemHealth.control_plane.uptime}</p>
                    </div>
                  </div>
                </div>

                <div className="space-y-3">
                  {systemHealth.services.map((service) => {
                  const updateInfo = updatesData?.services.find(s => s.service === service.name);
                  const hasUpdate = updateInfo?.has_update;
                  const checkError = updateInfo?.check_error;

                  return (
                    <div key={service.name} className={`glass-card p-4 ${hasUpdate ? 'border-orange-500/30' : ''}`}>
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <div className={`rounded-full p-2 ${
                            service.status === 'running' ? 'bg-emerald-500/10' :
                            service.health_status === 'unhealthy' ? 'bg-red-500/10' :
                            'bg-slate-500/10'
                          }`}>
                            {service.status === 'running' ? (
                              <Power size={16} className="text-emerald-400" />
                            ) : service.health_status === 'unhealthy' ? (
                              <ShieldAlert size={16} className="text-red-400" />
                            ) : (
                              <PowerOff size={16} className="text-slate-400" />
                            )}
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <p className="font-semibold text-white capitalize">{service.name.replace(/_/g, ' ')}</p>
                              {hasUpdate && (
                                <span className="flex items-center gap-1 rounded-full bg-orange-500/10 px-2 py-0.5 text-[9px] font-black uppercase tracking-widest text-orange-400">
                                  <ArrowUpCircle size={10} /> Update Available
                                </span>
                              )}
                              {!hasUpdate && checkError && checkError !== 'no_image_tag' && (
                                <span
                                  className="flex items-center gap-1 rounded-full bg-slate-500/10 px-2 py-0.5 text-[9px] font-black uppercase tracking-widest text-slate-500"
                                  title={`Update check failed: ${checkError}. Set GHCR_TOKEN in .env to enable registry checks.`}
                                >
                                  Check Failed
                                </span>
                              )}
                            </div>
                            <p className="text-[10px] font-mono text-slate-500">{service.image}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`rounded-full px-2 py-1 text-[9px] font-black uppercase tracking-widest ${
                            service.status === 'running' ? 'bg-emerald-500/10 text-emerald-400' :
                            service.health_status === 'unhealthy' ? 'bg-red-500/10 text-red-400' :
                            'bg-slate-500/10 text-slate-400'
                          }`}>
                            {service.status}
                          </span>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center justify-between gap-4 border-t border-white/5 pt-3 text-xs text-slate-500">
                        <div className="flex gap-4">
                          <span>Uptime: {service.uptime || 'N/A'}</span>
                          {service.restart_count > 0 && (
                            <span>Restarts: {service.restart_count}</span>
                          )}
                          {service.started_at && (
                            <span>Started: {new Date(service.started_at).toLocaleString()}</span>
                          )}
                        </div>
                        <div className="flex gap-1 shrink-0">
                          {service.status === 'running' && (
                            <>
                              <button
                                onClick={() => restartServiceMutation.mutate(service.name)}
                                disabled={restartServiceMutation.isPending}
                                className="glass-button px-3 py-2 text-[9px] font-black uppercase tracking-widest"
                              >
                                <RefreshCw size={12} /> Restart
                              </button>
                              <button
                                onClick={() => pullImageMutation.mutate(service.name)}
                                disabled={pullImageMutation.isPending || pullImageMutation.isPaused}
                                className={`glass-button px-3 py-2 text-[9px] font-black uppercase tracking-widest ${hasUpdate ? 'border-orange-500/50 text-orange-400' : ''}`}
                              >
                                <Cloud size={12} /> {hasUpdate ? 'Update' : 'Pull'}
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
                </div>
              </>
            ) : (
              <div className="flex h-48 items-center justify-center text-slate-500">
                <p>Unable to fetch system health. Control plane may be unreachable.</p>
              </div>
            )}
          </div>
        </section>
      )}

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

          {!editingUser && (
            <label className="space-y-2">
              <span className="text-xs font-black uppercase tracking-widest text-slate-500">
                Password <span className="text-red-400">*</span>
              </span>
              <input
                type="password"
                value={userForm.password}
                aria-label="Password"
                onChange={(event) => setUserForm((current) => ({ ...current, password: event.target.value }))}
                className="glass-input w-full"
                placeholder="Required for new users"
              />
              <p className="text-[10px] text-slate-500">New users must have a password to log in.</p>
            </label>
          )}

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
              ['Audiobookshelf API Key', 'audiobookshelf_api_key'],
            ].map(([label, key]) => (
              <label key={key} className="space-y-2">
                <span className="text-xs font-black uppercase tracking-widest text-slate-500">{label}</span>
                <input
                  type={label.toLowerCase().includes('token') || label.toLowerCase().includes('password') || label.toLowerCase().includes('api_key') || key.includes('api_key') ? 'password' : 'text'}
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
              {collectionDocs?.items?.map((item: { id: string; document: string; metadata?: Record<string, unknown> }) => (
                <div key={item.id} className="rounded-2xl border border-white/5 bg-black/40 p-4 font-mono text-[11px]">
                  <div className="mb-2 flex items-center justify-between border-b border-white/5 pb-2">
                    <span className="text-indigo-300">ID: {item.id}</span>
                    <span className="text-slate-500 uppercase tracking-widest">{String(item.metadata?.type || 'Record')}</span>
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

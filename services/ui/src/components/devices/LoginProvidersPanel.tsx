import { useId, useState } from 'react';
import PasswordField from '../ui/PasswordField';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { KeyRound, Plus, RefreshCw } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  createHubUser, getHubUsers, getLoginProviders, providerLabel, setLoginProviders, syncLoginProvider, syncSummary,
  updateHubUser, type HubUser, type LoginProvider, type NewHubUser,
} from '../../lib/homeSetup';
import { relativeTime } from '../../lib/wanderTrips';
import SetupSection from './SetupSection';
import { BTN, INPUT } from './setupStyles';

const PROVIDERS = ['homehub-login-providers'];
const USERS = ['homehub-users'];
const PERSONS = ['homehub-persons'];

function ProviderRow({ provider, onSynced }: { provider: LoginProvider; onSynced: () => void }) {
  const sync = useMutation({
    mutationFn: () => syncLoginProvider(provider.type),
    onSuccess: (r) => { toast.success(`${provider.label}: ${syncSummary(r)}`); onSynced(); },
    onError: (err) => { toast.error(failureText(err)); onSynced(); },
  });
  const last = provider.last_sync;
  const when = last?.ok ? last.at : last?.failed_at;
  const status = !provider.configured ? 'Not configured in this stack'
    : !provider.active ? 'Off: add it in Integrations'
      : provider.state === 'setup_retry' ? 'Retrying' : 'On';
  return (
    <li className="space-y-1 px-4 py-3" data-testid="login-provider">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-white">
            {provider.external ? provider.label : 'This hub'}
            {provider.primary && <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[11px] font-medium text-sky-200">Primary</span>}
          </p>
          <p className="text-xs text-slate-400">
            {provider.external ? status : 'Users and passwords kept on the hub'}
            {` · ${provider.users} ${provider.users === 1 ? 'user' : 'users'}`}
            {provider.inactive_users ? ` · ${provider.inactive_users} inactive` : ''}
          </p>
          {provider.external && provider.configured && (
            <p className={`break-words text-xs ${last && !last.ok ? 'text-amber-300/90' : 'text-slate-500'}`} data-testid="login-provider-sync">
              {syncSummary(last)}{when ? ` · ${relativeTime(when)}` : ''}
            </p>
          )}
        </div>
        {provider.external && provider.active && (
          <button type="button" className={BTN} disabled={sync.isPending} onClick={() => sync.mutate()}
            aria-label={`Sync ${provider.label} now`}>
            <RefreshCw size={13} className={sync.isPending ? 'animate-spin' : ''} /> Sync now
          </button>
        )}
      </div>
    </li>
  );
}

function UserRow({ user, onChanged }: { user: HubUser; onChanged: () => void }) {
  const change = useMutation({
    mutationFn: (body: { is_admin?: boolean; is_active?: boolean }) => updateHubUser(user.id, body),
    onSuccess: onChanged,
    onError: (err) => toast.error(failureText(err)),
  });
  const from = providerLabel(user.source);
  return (
    <li className="flex flex-wrap items-start justify-between gap-2 px-4 py-3" data-testid="hub-user">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-white">
          <span className="min-w-0 truncate">{user.name}</span>
          {from && <span className="rounded-full bg-violet-500/15 px-2 py-0.5 text-[11px] font-medium text-violet-200">{from}</span>}
          {user.is_admin && <span className="rounded-full bg-white/10 px-2 py-0.5 text-[11px] text-slate-200">Admin</span>}
        </p>
        <p className="break-words text-xs text-slate-400">
          {user.username ? `${user.username} · ` : ''}
          {user.provider?.removed ? 'Removed in Jarvis' : user.is_active ? 'Active' : 'Inactive'}
          {from ? ' · change in Jarvis' : user.hub_login ? ' · hub password' : ' · no hub login'}
        </p>
      </div>
      {user.editable && user.source === 'local' && (
        <span className="flex flex-wrap gap-2">
          <button type="button" className={BTN} disabled={change.isPending}
            onClick={() => change.mutate({ is_admin: !user.is_admin })}>
            {user.is_admin ? 'Remove admin' : 'Make admin'}
          </button>
          <button type="button" className={BTN} disabled={change.isPending}
            onClick={() => change.mutate({ is_active: !user.is_active })}>
            {user.is_active ? 'Deactivate' : 'Activate'}
          </button>
        </span>
      )}
    </li>
  );
}

function NewUserForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [body, setBody] = useState<NewHubUser>({ name: '', username: '', password: '', is_admin: false });
  const passwordId = useId();
  const add = useMutation({
    mutationFn: () => createHubUser({ ...body, name: body.name.trim(), username: body.username.trim().toLowerCase() }),
    onSuccess: (u) => { toast.success(`${u.name} can now log in to the hub.`); onDone(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const ready = body.name.trim() && body.username.trim() && body.password.length >= 8;
  return (
    <form className="space-y-2" data-testid="hub-user-editor" onSubmit={(e) => { e.preventDefault(); if (ready) add.mutate(); }}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="text-xs text-slate-400">Name
          <input className={INPUT} value={body.name} onChange={(e) => setBody({ ...body, name: e.target.value })} />
        </label>
        <label className="text-xs text-slate-400">Username
          <input className={INPUT} value={body.username} autoCapitalize="none" autoComplete="off"
            onChange={(e) => setBody({ ...body, username: e.target.value })} />
        </label>
        <div className="text-xs text-slate-400">
          <label htmlFor={passwordId}>Password</label>
          <PasswordField id={passwordId} className={INPUT} value={body.password} autoComplete="new-password"
            placeholder="8 characters or more" onChange={(e) => setBody({ ...body, password: e.target.value })} />
        </div>
      </div>
      <label className="inline-flex min-h-11 items-center gap-2 text-xs text-slate-200">
        <input type="checkbox" className="h-11 w-5 shrink-0" checked={body.is_admin}
          onChange={(e) => setBody({ ...body, is_admin: e.target.checked })} /> Admin
      </label>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={BTN} disabled={!ready || add.isPending}><Plus size={14} /> Add hub user</button>
        <button type="button" className={BTN} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/**
 * Setup > Users & logins: where the hub's users come from (the hub itself, Jarvis), which one is primary,
 * whether hub passwords still work, and the hub's users. Jarvis's users are changed in Jarvis; the hub's own
 * users are added and managed here.
 */
export default function LoginProvidersPanel() {
  const queryClient = useQueryClient();
  const providers = useQuery({ queryKey: PROVIDERS, queryFn: getLoginProviders, retry: false });
  const users = useQuery({ queryKey: USERS, queryFn: getHubUsers, retry: false });
  const [adding, setAdding] = useState(false);
  const refresh = () => {
    for (const key of [PROVIDERS, USERS, PERSONS]) void queryClient.invalidateQueries({ queryKey: key });
  };
  const save = useMutation({
    mutationFn: (body: { primary?: string; local_logins?: boolean }) => setLoginProviders(body),
    onSuccess: (data) => queryClient.setQueryData(PROVIDERS, data),
    onError: (err) => toast.error(failureText(err)),
  });
  const data = providers.data;
  const active = (data?.providers ?? []).filter((p) => p.active);
  const external = active.some((p) => p.external);
  const people = (users.data ?? []).filter((u) => u.source !== 'service' && u.source !== 'system');
  return (
    <SetupSection title="Users & logins" icon={KeyRound} testId="login-providers">
      {providers.isLoading && <p className="p-4 text-sm text-slate-500">Loading logins…</p>}
      {providers.error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(providers.error)}</p>}
      {data && (
        <>
          <ul className="divide-y divide-white/5">
            {data.providers.filter((p) => !p.external || p.configured || p.active).map((p) => (
              <ProviderRow key={p.type} provider={p} onSynced={refresh} />
            ))}
          </ul>
          <div className="grid grid-cols-1 gap-3 border-t border-white/5 px-4 py-3 sm:grid-cols-2">
            <label className="text-xs text-slate-400">Primary login
              <select className={INPUT} value={data.primary} disabled={save.isPending || active.length < 2}
                onChange={(e) => save.mutate({ primary: e.target.value })}>
                {active.map((p) => <option key={p.type} value={p.type}>{p.external ? p.label : 'This hub'}</option>)}
              </select>
            </label>
            <div className="text-xs text-slate-400">
              <span>Hub passwords</span>
              <label className="mt-1 flex min-h-11 items-center gap-2 text-slate-200">
                <input type="checkbox" role="switch" className="h-11 w-5 shrink-0" checked={data.local_logins}
                  aria-checked={data.local_logins} aria-label="Allow logins with hub passwords"
                  disabled={save.isPending || !external}
                  onChange={(e) => save.mutate({ local_logins: e.target.checked })} />
                {data.local_logins ? 'Hub users can log in' : 'Only Jarvis accounts can log in'}
              </label>
              {!external && <p className="text-slate-500">Always on while no other login is set up.</p>}
            </div>
          </div>
          {data.standalone_users.length > 0 && external && (
            <p className="break-words border-t border-white/5 px-4 py-2 text-xs text-slate-500" data-testid="standalone-users">
              Hub-only users (no Jarvis account with their username): {data.standalone_users.join(', ')}
            </p>
          )}
        </>
      )}
      <h3 className="border-t border-white/5 px-4 pt-3 text-xs font-semibold uppercase tracking-wide text-slate-400">Users</h3>
      {users.error && <p className="px-4 py-2 text-sm text-amber-300/90" role="alert">{failureText(users.error)}</p>}
      <ul className="divide-y divide-white/5">
        {people.map((u) => <UserRow key={u.id} user={u} onChanged={refresh} />)}
      </ul>
      <div className="border-t border-white/5 px-4 py-3">
        {adding
          ? <NewUserForm onDone={() => { setAdding(false); refresh(); }} onCancel={() => setAdding(false)} />
          : <button type="button" className={BTN} onClick={() => setAdding(true)}><Plus size={14} /> New hub user</button>}
      </div>
    </SetupSection>
  );
}

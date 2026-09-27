import { useState } from 'react';
import { KeyRound, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';

type Service = 'home_assistant' | 'nextcloud' | 'audiobookshelf';

const SERVICES: Array<{ id: Service; label: string; needsPassword: boolean; hint: string }> = [
  {
    id: 'nextcloud',
    label: 'Nextcloud',
    needsPassword: false,
    hint: 'Jarvis mints an app password for them — nothing to type',
  },
  {
    id: 'home_assistant',
    label: 'Home Assistant',
    needsPassword: true,
    hint: 'They type their Home Assistant password once; it is traded for a token and never stored',
  },
  {
    id: 'audiobookshelf',
    label: 'Audiobookshelf',
    needsPassword: true,
    hint: 'They type their Audiobookshelf password once; it is traded for a token and never stored',
  },
];

/**
 * Onboarding helper: turn a service login into a long-lived token so nobody
 * has to keep a password in Jarvis. Nextcloud needs no password at all — the
 * admin mints an app password. The typed password is sent once and discarded.
 */
export default function ServiceAccessSetup({
  username,
  services,
}: {
  username: string;
  services?: string[];
}) {
  const [pending, setPending] = useState<Service | null>(null);
  const [done, setDone] = useState<Service[]>([]);
  const [asking, setAsking] = useState<Service | null>(null);
  const [password, setPassword] = useState('');

  const offered = SERVICES.filter((s) => {
    if (!services?.length) return true;
    return services.some((label) => s.label.toLowerCase().includes(label.toLowerCase()));
  });

  const submit = async (service: Service) => {
    setPending(service);
    try {
      const res = await api.setUpServiceToken(username, service, password || undefined);
      if (res.success) {
        setDone((prev) => [...prev, service]);
        setPassword('');
        setAsking(null);
        toast.success(res.message || 'Access set up');
      } else {
        toast.error(res.detail || res.message || 'Could not set up access');
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not set up access');
    } finally {
      setPending(null);
    }
  };

  const activate = (service: { id: Service; needsPassword: boolean }) => {
    if (!service.needsPassword) {
      void submit(service.id);
      return;
    }
    setAsking(service.id);
    setPassword('');
  };

  if (offered.length === 0) return null;

  return (
    <div className="mt-2 space-y-1.5" data-testid={`service-access-${username}`}>
      {offered.map((service) => {
        const isDone = done.includes(service.id);
        return (
          <div key={service.id} className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => activate(service)}
              disabled={pending === service.id || isDone}
              className="glass-button min-h-9 px-2.5 py-1.5 text-[11px] disabled:opacity-60"
              title={service.hint}
            >
              {pending === service.id ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <KeyRound size={12} />
              )}
              {isDone ? `${service.label} ready` : `Set up ${service.label}`}
            </button>
            {!isDone && <span className="text-[10px] text-slate-500 hidden sm:inline">{service.hint}</span>}
          </div>
        );
      })}
      {asking && (
        <form
          className="flex items-center gap-2 pt-1"
          onSubmit={(event) => {
            event.preventDefault();
            if (!password.trim()) {
              toast.error('A password is required to create the token');
              return;
            }
            void submit(asking);
          }}
        >
          <input
            type="password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={`${username}'s password — used once, not stored`}
            aria-label={`${asking} password`}
            className="glass-input flex-1 px-2.5 py-1.5 text-[11px]"
          />
          <button type="submit" disabled={pending === asking} className="glass-button min-h-9 px-2.5 py-1.5 text-[11px]">
            {pending === asking ? <Loader2 size={12} className="animate-spin" /> : 'Create token'}
          </button>
          <button
            type="button"
            onClick={() => {
              setAsking(null);
              setPassword('');
            }}
            className="glass-button min-h-9 px-2.5 py-1.5 text-[11px]"
          >
            Cancel
          </button>
        </form>
      )}
    </div>
  );
}

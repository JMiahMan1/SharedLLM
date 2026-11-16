import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';

const KEY = 'malwarebazaar_auth_key';
const COMMON_SENSE = 'app_vetting_common_sense_enabled';
const BUTTON = 'min-h-11 shrink-0 rounded-xl border px-4 text-sm font-semibold transition-colors disabled:opacity-50';

/**
 * Admin: the outside checks the Apps card runs on children's apps
 * (docs/APP_VETTING.md). The abuse.ch key is a secret: the server only ever
 * answers "***" for it, so this box says whether one is saved and never shows it.
 */
export default function AppChecksPanel() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api.getSettings() });
  const [key, setKey] = useState('');
  const value = (k: string) => settings.data?.find((s) => s.key === k)?.value ?? null;
  const keySaved = !!value(KEY);
  const commonSense = (value(COMMON_SENSE) ?? 'true').toLowerCase() !== 'false';

  const save = useMutation({
    mutationFn: ({ k, v }: { k: string; v: string; done: string }) => api.updateSetting(k, v),
    onSuccess: (_d, { done }) => {
      toast.success(done);
      setKey('');
      void queryClient.invalidateQueries({ queryKey: ['settings'] });
      void queryClient.invalidateQueries({ queryKey: ['guardian-apps'] });
    },
    onError: (err) => toast.error(errorText(err, 'That did not save. Try again.')),
  });

  return (
    <section className="glass-panel min-w-0 space-y-4 overflow-hidden p-4 sm:p-6" data-testid="app-checks-panel" aria-labelledby="app-checks-heading">
      <div>
        <h3 id="app-checks-heading" className="flex items-center gap-2 text-lg font-bold text-white">
          <ShieldCheck size={20} className="text-violet-300" aria-hidden />
          App checks
        </h3>
        <p className="mt-1 text-sm text-slate-400">
          What the Apps card looks up for each app on a child&apos;s device, beyond Google Play. The Jarvis app is never checked.
        </p>
      </div>

      {settings.isError && <p className="text-sm text-amber-300" role="alert">The settings could not be loaded.</p>}

      <form
        className="min-w-0 space-y-2 rounded-xl border border-white/10 bg-white/[0.02] p-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (key.trim()) save.mutate({ k: KEY, v: key.trim(), done: 'abuse.ch key saved. Apps are checked on their next refresh.' });
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor="abuse-ch-key" className="text-sm font-semibold text-white">Malware check (abuse.ch MalwareBazaar)</label>
          <span
            className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${keySaved ? 'border-emerald-400/30 text-emerald-200' : 'border-amber-400/30 text-amber-200'}`}
            data-testid="abuse-ch-key-state"
          >
            {settings.isLoading ? 'Checking' : keySaved ? 'Key saved' : 'Not set up'}
          </span>
        </div>
        <p className="text-xs leading-relaxed text-slate-400">
          Only each app file&apos;s fingerprint (its SHA-256) is sent, never the app, its name or whose device it is on.
          The key is kept on the server and is not shown again once saved.
        </p>
        <div className="flex min-w-0 flex-wrap gap-2">
          <input
            id="abuse-ch-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={keySaved ? 'Paste a new key to replace it' : 'Paste your Auth-Key'}
            className="glass-input min-h-11 min-w-0 flex-1 basis-56 px-3 text-base sm:text-sm"
            data-testid="abuse-ch-key"
          />
          <button type="submit" disabled={!key.trim() || save.isPending}
            className={`${BUTTON} border-sky-400/30 bg-sky-500/15 text-sky-100 hover:bg-sky-500/25`} data-testid="abuse-ch-key-save">
            Save key
          </button>
          {keySaved && (
            <button type="button" disabled={save.isPending}
              onClick={() => save.mutate({ k: KEY, v: '', done: 'abuse.ch key removed. The malware check is off.' })}
              className={`${BUTTON} border-white/10 bg-white/5 text-slate-200 hover:bg-white/10`} data-testid="abuse-ch-key-remove">
              Remove
            </button>
          )}
        </div>
      </form>

      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] p-3">
        <div className="min-w-0 flex-1 basis-60">
          <p className="text-sm font-semibold text-white" id="common-sense-label">Common Sense Media ages</p>
          <p className="text-xs leading-relaxed text-slate-400">
            Looks each app up by its name and uses Common Sense&apos;s age next to the Play rating; the older age decides.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={commonSense}
          aria-labelledby="common-sense-label"
          disabled={!settings.data || save.isPending}
          onClick={() => save.mutate({
            k: COMMON_SENSE,
            v: commonSense ? 'false' : 'true',
            done: commonSense ? 'Common Sense Media lookups are off' : 'Common Sense Media lookups are on',
          })}
          className="flex h-11 w-16 shrink-0 items-center justify-center disabled:opacity-50"
          data-testid="common-sense-switch"
        >
          <span className={`relative block h-8 w-14 rounded-full border transition-colors ${commonSense ? 'border-emerald-400/40 bg-emerald-500/40' : 'border-white/15 bg-white/10'}`} aria-hidden>
            <span className={`absolute top-[3px] h-6 w-6 rounded-full bg-white shadow transition-all ${commonSense ? 'left-[26px]' : 'left-[3px]'}`} />
          </span>
        </button>
      </div>

      <p className="text-[11px] leading-relaxed text-slate-500">
        Every app is also matched on the device&apos;s own report against a bundled list of known stalkerware
        (Echap&apos;s Stalkerware Indicators, CC BY 4.0), and apps from outside Google Play that hold risky access are flagged.
        Neither sends anything anywhere.
      </p>
    </section>
  );
}

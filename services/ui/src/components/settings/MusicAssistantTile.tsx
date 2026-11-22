import { useState } from 'react';
import { AlertCircle, CheckCircle, House, Info, ListMusic, RefreshCcw, Save } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { UserProfile } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { musicAssistantIsError, musicAssistantLine, offersHousehold, offersNewLogin } from '../../lib/musicAssistant';
import Modal from '../ui/Modal';
import SecretInput from '../ui/SecretInput';
import HelpTooltip from '../ui/HelpTooltip';

const STATUS_KEY = ['music-assistant-status'];

/**
 * Music Assistant in Accounts & Integrations (docs/INTEGRATIONS.md, "Music Assistant").
 *
 * A person uses their own Music Assistant when they saved its URL here, else the
 * household's (wherever HomeHub's Music Assistant integration points), with their
 * own Music Assistant account on it so their play history and resume points are
 * theirs. The card shows which, and whether it answers. A personal server that does
 * not answer is an error with "Use the household Music Assistant" as the explicit
 * way back: nothing switches servers, or logins, by itself.
 */
export default function MusicAssistantTile({ userData }: { userData?: UserProfile | null }) {
  const [isOpen, setIsOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: STATUS_KEY, queryFn: () => api.getMusicAssistantStatus(), retry: false, staleTime: 60_000 });
  const data = status.data;
  const line = status.isError ? 'Status unavailable' : musicAssistantLine(data, status.isLoading);
  const bad = status.isError || musicAssistantIsError(data);
  const tokenSaved = (userData?.saved_credentials ?? []).includes('mass_token');

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: STATUS_KEY });
    void queryClient.invalidateQueries({ queryKey: ['me'] });
  };

  const save = useMutation({
    mutationFn: (body: Partial<UserProfile>) => api.updateProfile(body),
    onSuccess: (_d, body) => {
      refresh();
      setToken('');
      const cleared = (body as { clear_fields?: string[] }).clear_fields ?? [];
      if (cleared.includes('mass_url')) {
        setUrl('');
        toast.success('You now use the household Music Assistant');
      } else if (cleared.includes('mass_token')) {
        toast.success('Your old login was removed; Jarvis is getting you a new one');
      } else {
        setIsOpen(false);
        toast.success('Music Assistant settings saved');
      }
    },
    onError: (error: unknown) => toast.error(errorText(error, 'Music Assistant settings were not saved')),
  });

  const open = () => {
    setUrl(data?.own_url ?? '');
    setToken('');
    setIsOpen(true);
    refresh();
  };

  const urlValue = url.trim();
  const urlValid = urlValue === '' || /^https?:\/\/[^\s/]+/.test(urlValue);
  const changes: Record<string, unknown> = {};
  if (urlValue && urlValue !== (data?.own_url ?? '')) changes.mass_url = urlValue;
  if (token.trim()) changes.mass_token = token.trim();
  const canSave = urlValid && Object.keys(changes).length > 0 && !save.isPending;

  return (
    <>
      <div className="glass-panel p-4 md:p-6 flex flex-row md:flex-col items-center text-left md:text-center gap-3 md:gap-4 group hover:border-purple-500/30 transition-all duration-500" data-testid="music-assistant-tile">
        <div className="p-3 md:p-4 rounded-2xl shrink-0 bg-fuchsia-500/20 text-fuchsia-400 group-hover:scale-110 transition-transform duration-500">
          <ListMusic className="w-6 h-6 md:w-8 md:h-8" />
        </div>
        <div className="flex-1 md:flex-none min-w-0">
          <h3 className="font-bold text-white break-words">Music Assistant</h3>
          <p className={`text-[11px] mt-1 break-words ${bad ? 'text-rose-300' : 'text-slate-400'}`} data-testid="integration-status-Music Assistant">{line}</p>
        </div>
        <button onClick={open} aria-label="Manage Music Assistant" className="glass-button shrink-0 md:w-full text-[10px] uppercase font-bold tracking-widest md:mt-2 min-h-11 px-3">
          {bad ? 'Fix' : 'Manage'}
        </button>
        <div className="hidden md:flex items-center gap-2 mt-2">
          <div className={`w-1.5 h-1.5 rounded-full ${data?.state === 'ok' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : bad ? 'bg-rose-500' : 'bg-slate-600'}`} />
          <span className="text-[9px] uppercase font-black tracking-tighter text-slate-500">{data?.state === 'ok' ? 'Connected' : bad ? 'Problem' : 'Checking'}</span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title="Music Assistant">
        <div className="space-y-5" data-testid="music-assistant-dialog">
          <div role={bad ? 'alert' : 'status'} data-testid="music-assistant-status"
            className={`p-4 rounded-xl border flex gap-3 text-xs ${bad ? 'bg-rose-500/10 border-rose-500/20 text-rose-100' : 'bg-emerald-500/10 border-emerald-500/20 text-slate-200'}`}>
            {bad ? <AlertCircle size={20} className="shrink-0 text-rose-300" aria-hidden /> : <CheckCircle size={20} className="shrink-0 text-emerald-400" aria-hidden />}
            <div className="min-w-0 leading-relaxed">
              <p className="font-bold text-white mb-1 break-words">{line}</p>
              <p className="break-words">{status.isError ? errorText(status.error, 'Music Assistant status could not be read') : data?.message}</p>
              {data?.url && <p className="mt-1 text-slate-400 break-all">Server: {data.url.replace(/^https?:\/\//, '')}</p>}
            </div>
          </div>

          {offersHousehold(data) && (
            <button type="button" onClick={() => save.mutate({ clear_fields: ['mass_url'] } as Partial<UserProfile>)}
              disabled={save.isPending}
              className="glass-button w-full min-h-11 py-3 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50">
              <House size={16} aria-hidden />
              Use the household Music Assistant
            </button>
          )}
          {offersNewLogin(data) && (
            <button type="button" onClick={() => save.mutate({ clear_fields: ['mass_token'] } as Partial<UserProfile>)}
              disabled={save.isPending}
              className="glass-button w-full min-h-11 py-3 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50">
              <RefreshCcw size={16} aria-hidden />
              Get a new login
            </button>
          )}

          <div>
            <div className="flex items-center mb-2">
              <label htmlFor="mass-own-url" className="text-[10px] text-slate-400 uppercase font-black tracking-widest">
                Your own server URL (optional)
              </label>
              <HelpTooltip docName="INTEGRATIONS.md" sectionTitle="Music Assistant" label="Your own server URL" />
            </div>
            <input id="mass-own-url" type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)}
              placeholder={data?.household_url ? 'Blank: the household Music Assistant' : 'http://music.example.internal:8095'}
              className="glass-input w-full text-sm py-3 bg-black/20 focus:bg-black/40" />
            {!urlValid && <p className="mt-1 text-[11px] text-amber-300" role="alert">Start the address with http:// or https://</p>}
            <p className="mt-1 text-[11px] text-slate-400 leading-relaxed">
              Only if you run Music Assistant for your own library.
              {data?.household_error && !data.household_url ? ` ${data.household_error}` : ''}
            </p>
          </div>

          <div>
            <span className="mb-2 block text-[10px] text-slate-400 uppercase font-black tracking-widest">Access token</span>
            <SecretInput label="Music Assistant access token" value={token} saved={tokenSaved} onChange={setToken}
              placeholder={urlValue ? 'A long-lived token from your Music Assistant' : 'Blank: Jarvis makes your account'}
              className="text-sm py-3 bg-black/20 focus:bg-black/40" />
          </div>

          {data?.note && (
            <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl flex gap-3" data-testid="music-assistant-note">
              <Info className="text-indigo-400 shrink-0" size={18} aria-hidden />
              <p className="text-[11px] text-slate-300 leading-relaxed">{data.note}</p>
            </div>
          )}

          <button type="button" onClick={() => save.mutate(changes as Partial<UserProfile>)} disabled={!canSave}
            className="glass-button w-full min-h-11 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50">
            <Save size={16} aria-hidden />
            {save.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </Modal>
    </>
  );
}

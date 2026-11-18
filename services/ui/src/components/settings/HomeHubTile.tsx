import { useState } from 'react';
import { AlertCircle, CheckCircle, House, RefreshCcw, Save } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { useIntegrations } from '../../hooks/useIntegrations';
import Modal from '../ui/Modal';
import { HOMEHUB_RESTART_NOTE, HOMEHUB_SETTING, homehubStatusLine } from '../../lib/homehub';

/**
 * HomeHub in Accounts & Integrations, for admins only. HomeHub is the house's
 * own hub (it supersedes Home Assistant here); its address is the household
 * setting `homehub_url`, never a per-person login, and the services sign in to
 * it with the stack's own secret. A blank address turns the integration off,
 * which hides every hub page, widget and setting instead of failing. "Test"
 * checks the address answers as a HomeHub without sending it any secret.
 */
export default function HomeHubTile() {
  const [isOpen, setIsOpen] = useState(false);
  const [value, setValue] = useState('');
  const [testResult, setTestResult] = useState<{ status: 'SUCCESS' | 'ERROR'; message?: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const queryClient = useQueryClient();
  const { homehub: running } = useIntegrations();
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api.getSettings(), retry: false });
  const rows = Array.isArray(settings.data) ? settings.data : [];
  const saved = rows.find((s) => s.key === HOMEHUB_SETTING)?.value?.trim() || null;
  const line = homehubStatusLine(running, saved);

  const save = useMutation({
    mutationFn: (address: string) => api.updateSetting(HOMEHUB_SETTING, address),
    onSuccess: (_data, address) => {
      void queryClient.invalidateQueries({ queryKey: ['settings'] });
      setIsOpen(false);
      toast.success(address ? `HomeHub address saved. ${HOMEHUB_RESTART_NOTE}` : `HomeHub turned off. ${HOMEHUB_RESTART_NOTE}`);
    },
    onError: (error: unknown) => toast.error(errorText(error, 'The HomeHub address was not saved')),
  });

  const open = () => {
    setValue(saved ?? '');
    setTestResult(null);
    setIsOpen(true);
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await api.testConnection('HomeHub', { homehub_url: value.trim() }));
    } catch (error: unknown) {
      setTestResult({ status: 'ERROR', message: errorText(error, 'HomeHub could not be reached') });
    } finally {
      setTesting(false);
    }
  };

  const address = value.trim();
  const valid = address === '' || /^https?:\/\/[^\s/]+/.test(address);

  return (
    <>
      <div className="glass-panel p-4 md:p-6 flex flex-row md:flex-col items-center text-left md:text-center gap-3 md:gap-4 group hover:border-purple-500/30 transition-all duration-500" data-testid="homehub-tile">
        <div className="p-3 md:p-4 rounded-2xl shrink-0 bg-sky-500/20 text-sky-400 group-hover:scale-110 transition-transform duration-500">
          <House className="w-6 h-6 md:w-8 md:h-8" />
        </div>
        <div className="flex-1 md:flex-none min-w-0">
          <h3 className="font-bold text-white break-words">HomeHub</h3>
          <p className="text-[11px] text-slate-400 mt-1 break-words" data-testid="integration-status-HomeHub">{line}</p>
        </div>
        <button onClick={open} aria-label="Manage HomeHub" className="glass-button shrink-0 md:w-full text-[10px] uppercase font-bold tracking-widest md:mt-2 min-h-11 px-3">
          {running || saved ? 'Manage' : 'Connect'}
        </button>
        <div className="hidden md:flex items-center gap-2 mt-2">
          <div className={`w-1.5 h-1.5 rounded-full ${running ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-slate-600'}`} />
          <span className="text-[9px] uppercase font-black tracking-tighter text-slate-500">{running ? 'On' : 'Off'}</span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title="HomeHub">
        <div className="space-y-5" data-testid="homehub-dialog">
          <p className="text-xs text-slate-300 leading-relaxed">
            The house&apos;s own hub: rooms, devices, climate, cameras, energy, schedules and automations. Its address is
            for the whole household. Leave it blank to turn HomeHub off: its pages, widgets and settings then disappear.
          </p>

          <div>
            <label htmlFor="homehub-url" className="mb-2 block text-[10px] text-slate-400 uppercase font-black tracking-widest">
              HomeHub address
            </label>
            <input
              id="homehub-url"
              type="url"
              inputMode="url"
              value={value}
              onChange={(e) => { setValue(e.target.value); setTestResult(null); }}
              placeholder="http://homehub.example.internal:8123"
              className="glass-input w-full text-sm py-3 bg-black/20 focus:bg-black/40"
            />
            {!valid && <p className="mt-1 text-[11px] text-amber-300" role="alert">Start the address with http:// or https://</p>}
          </div>

          <p className="text-[11px] text-slate-400 leading-relaxed">{HOMEHUB_RESTART_NOTE}</p>

          {testResult && (
            <div role="status" data-testid="homehub-test-result"
              className={`p-3 rounded-xl border flex gap-3 text-xs ${testResult.status === 'SUCCESS'
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-200' : 'bg-rose-500/10 border-rose-500/20 text-rose-200'}`}>
              {testResult.status === 'SUCCESS' ? <CheckCircle size={18} className="shrink-0" aria-hidden /> : <AlertCircle size={18} className="shrink-0" aria-hidden />}
              <span className="min-w-0 break-words">{testResult.message || (testResult.status === 'SUCCESS' ? 'Connected' : 'Not reachable')}</span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <button type="button" onClick={() => void test()} disabled={testing || address === '' || !valid}
              className="glass-button min-h-11 py-3 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50">
              <RefreshCcw size={16} className={testing ? 'animate-spin' : undefined} />
              {testing ? 'Testing...' : 'Test'}
            </button>
            <button type="button" onClick={() => save.mutate(address)} disabled={save.isPending || !valid || address === (saved ?? '')}
              className="glass-button min-h-11 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50">
              <Save size={16} />
              {address === '' && saved ? 'Turn off' : 'Save'}
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}

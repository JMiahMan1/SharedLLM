import { useState } from 'react';
import { CheckCircle, LockKeyhole, Save, ShieldCheck } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { UserProfile } from '../../services/api';
import { INTEGRATIONS, householdKeyLine } from '../../lib/credentialForm';
import { errorText } from '../../lib/errorText';
import Modal from '../ui/Modal';
import SecretInput from '../ui/SecretInput';
import HelpTooltip from '../ui/HelpTooltip';

const ABUSE_CH = INTEGRATIONS.find((i) => i.id === 'abuse_ch')!;
const FIELD = ABUSE_CH.fields[0];

/**
 * The abuse.ch Auth-Key in Accounts & Integrations (docs/APP_VETTING.md).
 *
 * App checks look each app file's fingerprint up on abuse.ch MalwareBazaar.
 * The default user's key is the household default: everyone without their own
 * uses it, no grant needed. So this card says which key applies ("Using the
 * household key") and lets a person add or remove their own. The key is never
 * shown again once saved (no reveal eye); identity checks it with abuse.ch
 * before saving and answers why when it does not.
 */
export default function AbuseChKeyTile({ userData }: { userData?: UserProfile | null }) {
  const [isOpen, setIsOpen] = useState(false);
  const [value, setValue] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const queryClient = useQueryClient();
  const isDefault = Boolean(userData?.is_system_default);
  const status = userData?.abuse_ch_status ?? 'not_set_up';
  const saved = (userData?.saved_credentials ?? []).includes(FIELD.key);
  const line = householdKeyLine(status, isDefault);
  const usable = status === 'own' || status === 'household';

  const save = useMutation({
    mutationFn: (body: Partial<UserProfile>) => api.updateProfile(body),
    onSuccess: (_data, body) => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setValue('');
      setConfirmRemove(false);
      setIsOpen(false);
      toast.success('clear_fields' in body ? 'Your abuse.ch key was removed' : 'abuse.ch accepted the key; it is saved');
    },
    onError: (error: unknown) => toast.error(errorText(error, 'The key was not saved')),
  });

  const open = () => {
    setValue('');
    setConfirmRemove(false);
    setIsOpen(true);
  };

  return (
    <>
      <div className="glass-panel p-4 md:p-6 flex flex-row md:flex-col items-center text-left md:text-center gap-3 md:gap-4 group hover:border-purple-500/30 transition-all duration-500" data-testid="abuse-ch-tile">
        <div className="p-3 md:p-4 rounded-2xl shrink-0 bg-emerald-500/20 text-emerald-400 group-hover:scale-110 transition-transform duration-500">
          <ShieldCheck className="w-6 h-6 md:w-8 md:h-8" />
        </div>
        <div className="flex-1 md:flex-none min-w-0">
          <h3 className="font-bold text-white break-words">{ABUSE_CH.name}</h3>
          <p className="text-[11px] text-slate-400 mt-1 break-words" data-testid="integration-status-abuse.ch">{line}</p>
        </div>
        <button onClick={open} aria-label={`Manage ${ABUSE_CH.name}`} className="glass-button shrink-0 md:w-full text-[10px] uppercase font-bold tracking-widest md:mt-2 min-h-11 px-3">
          {status === 'own' ? 'Manage' : usable ? 'Details' : 'Set up'}
        </button>
        <div className="hidden md:flex items-center gap-2 mt-2">
          <div className={`w-1.5 h-1.5 rounded-full ${usable ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-slate-600'}`} />
          <span className="text-[9px] uppercase font-black tracking-tighter text-slate-500">{usable ? 'Checking apps' : 'Not set up'}</span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={ABUSE_CH.name}>
        <div className="space-y-5" data-testid="abuse-ch-dialog">
          <p className="text-xs text-slate-300 leading-relaxed">
            App checks look up each app file on children&apos;s devices on abuse.ch MalwareBazaar, a public list of known
            malware. {ABUSE_CH.help}
          </p>

          <div className="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex gap-3" data-testid="abuse-ch-status">
            <CheckCircle className={usable ? 'text-emerald-400 shrink-0' : 'text-slate-500 shrink-0'} size={20} aria-hidden />
            <div className="text-xs text-slate-300 leading-relaxed min-w-0">
              <p className="font-bold text-white mb-1">{line}</p>
              {isDefault
                ? 'This account holds the household key: everyone who has not set their own uses it. An admin can also set it in Admin > Users & Devices.'
                : status === 'household'
                  ? 'An admin set a key for the whole household, so you need nothing here. Add your own only if your devices should use it instead.'
                  : status === 'own'
                    ? 'Your devices use your own key. Remove it to use the household key, if one is set.'
                    : 'No household key is set yet. Add your own here, or ask an admin to add one for everyone.'}
            </div>
          </div>

          <div>
            <div className="flex items-center mb-2">
              <span className="text-[10px] text-slate-400 uppercase font-black tracking-widest">
                {isDefault ? 'Household abuse.ch Auth-Key' : status === 'own' ? 'Your abuse.ch Auth-Key' : 'Your own abuse.ch Auth-Key (optional)'}
              </span>
              <HelpTooltip docName="INTEGRATIONS.md" sectionTitle="abuse.ch" label={FIELD.label} />
            </div>
            <SecretInput
              label={FIELD.label}
              value={value}
              saved={saved}
              onChange={setValue}
              placeholder="Paste the Auth-Key from auth.abuse.ch"
              className="text-sm py-3 bg-black/20 focus:bg-black/40"
            />
          </div>

          <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl flex gap-3">
            <LockKeyhole className="text-indigo-400 shrink-0" size={18} aria-hidden />
            <p className="text-[11px] text-slate-300 leading-relaxed">
              Checked with abuse.ch before it is saved, stored encrypted, and never shown again: a saved key shows as Saved.
            </p>
          </div>

          <button
            type="button"
            onClick={() => save.mutate({ [FIELD.key]: value.trim() } as Partial<UserProfile>)}
            disabled={save.isPending || value.trim() === ''}
            className="glass-button w-full min-h-11 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
          >
            <Save size={18} />
            {save.isPending ? 'Checking with abuse.ch...' : 'Save'}
          </button>

          {saved && (
            <div className="border-t border-white/5 pt-3">
              {confirmRemove ? (
                <div className="flex flex-wrap items-center gap-3 text-xs text-rose-200" role="alert">
                  {isDefault ? 'Remove the household key? Nobody without their own key will be checked.' : 'Remove your key?'}
                  <button type="button" className="font-semibold underline min-h-11 px-2"
                    onClick={() => save.mutate({ clear_fields: [FIELD.key] } as Partial<UserProfile>)}>
                    Remove
                  </button>
                  <button type="button" className="text-slate-300 min-h-11 px-2" onClick={() => setConfirmRemove(false)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button type="button" className="text-xs text-rose-300 hover:underline min-h-11" onClick={() => setConfirmRemove(true)}>
                  {isDefault ? 'Remove the household key' : 'Remove my key'}
                </button>
              )}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}

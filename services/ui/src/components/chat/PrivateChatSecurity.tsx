import { useEffect, useState, type FormEvent } from 'react';
import { ChevronRight, Fingerprint, KeyRound, Loader2, Timer, TriangleAlert } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { changePasswordRequest } from '../../lib/privateChatCrypto';
import { getLockAfter, LOCK_OPTIONS, setLockAfter, type LockAfter } from '../../lib/privateChatSession';
import { enableDeviceUnlock } from '../../lib/privateChatDeviceUnlock';
import { deviceUnlockAvailable, deviceUnlockEnabled, deviceUnlockError, deviceUnlockPlugin } from '../../plugins/deviceUnlock';
import Sheet from './Sheet';
import { errorText, MIN_PASSWORD_LENGTH, passwordProblem } from './privateChatModel';

type Mode = 'menu' | 'password' | 'phone';

const inputClass = 'glass-input min-h-11 w-full text-base sm:text-sm';

function PasswordField({ label, value, onChange, testId, autoComplete, autoFocus }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  testId: string;
  autoComplete: string;
  autoFocus?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-slate-300">{label}</span>
      <input
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        autoCapitalize="off"
        spellCheck={false}
        autoFocus={autoFocus}
        data-testid={testId}
        className={inputClass}
      />
    </label>
  );
}

/**
 * The private chat's password and unlock settings, from the unlocked chat:
 * change the password (either parent), and on an Android phone turn "Unlock
 * with this phone's lock" on or off for this device. Neither re-encrypts
 * anything: the chat's key stays the same.
 */
export default function PrivateChatSecurity({
  token,
  partnerName,
  onChanged,
  onClose,
}: {
  token: string;
  partnerName: string;
  /** The password changed: the chat's key record is new. */
  onChanged: () => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<Mode>('menu');
  const [lockAfter, setLockAfterState] = useState<LockAfter>(() => getLockAfter(token));
  const [phone, setPhone] = useState<{ available: boolean; enabled: boolean }>({ available: false, enabled: false });
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    void (async () => {
      const available = await deviceUnlockAvailable();
      const enabled = available ? await deviceUnlockEnabled(token) : false;
      if (live) setPhone({ available, enabled });
    })();
    return () => {
      live = false;
    };
  }, [token]);

  const reset = (next: Mode) => {
    setCurrent('');
    setPassword('');
    setConfirm('');
    setUnderstood(false);
    setError('');
    setMode(next);
  };

  const problem = passwordProblem(password, confirm) ?? (password && password === current ? 'Choose a password different from the current one.' : null);

  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (!current || problem || !understood || busy) return;
    setBusy(true);
    setError('');
    try {
      // Always from the server's current record, never a cached one.
      const info = await api.getPrivateChat(token);
      const body = await changePasswordRequest(info, current, password);
      await api.changePrivateChatPassword(token, body);
      toast.success(`Password changed. Tell ${partnerName} the new one in person.`);
      onChanged();
      reset('menu');
      onClose();
    } catch (e) {
      setError(errorText(e, 'Could not change the password'));
    } finally {
      setCurrent('');
      setBusy(false);
    }
  };

  const turnOnPhone = async (event: FormEvent) => {
    event.preventDefault();
    const plugin = deviceUnlockPlugin();
    if (!plugin || !current || busy) return;
    setBusy(true);
    setError('');
    try {
      const info = await api.getPrivateChat(token);
      await enableDeviceUnlock(plugin, token, current, info);
      setPhone({ available: true, enabled: true });
      toast.success('This phone can unlock the chat now');
      reset('menu');
    } catch (e) {
      setError(deviceUnlockError(e, 'Could not turn on the phone lock'));
    } finally {
      setCurrent('');
      setBusy(false);
    }
  };

  const chooseLock = (value: LockAfter) => {
    try {
      setLockAfter(token, value);
      setLockAfterState(value);
    } catch (e) {
      setError(errorText(e, 'Could not save the lock setting'));
    }
  };

  const turnOffPhone = async () => {
    const plugin = deviceUnlockPlugin();
    if (!plugin || busy) return;
    setBusy(true);
    setError('');
    try {
      await plugin.disable({ token });
      setPhone((p) => ({ ...p, enabled: false }));
      toast.success('Phone lock turned off for this chat');
    } catch (e) {
      setError(deviceUnlockError(e, 'Could not turn off the phone lock'));
    } finally {
      setBusy(false);
    }
  };

  if (mode === 'password') {
    return (
      <Sheet title="Change password" onClose={onClose} testId="private-change-password">
        <form onSubmit={(event) => void changePassword(event)} className="space-y-3">
          <p className="text-sm leading-relaxed text-slate-300">
            Everything already in the chat stays readable. {partnerName} will need the new password the next time they unlock it.
          </p>
          <PasswordField label="Current password" value={current} onChange={setCurrent} testId="private-current-password" autoComplete="current-password" autoFocus />
          <PasswordField label="New password" value={password} onChange={setPassword} testId="private-next-password" autoComplete="new-password" />
          <PasswordField label="Type the new one again" value={confirm} onChange={setConfirm} testId="private-next-confirm" autoComplete="new-password" />
          {password && problem && (
            <p className="text-xs text-amber-200" role="status">
              {problem}
            </p>
          )}
          <div className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-sm text-amber-100" role="note">
            <p className="flex items-center gap-2 font-semibold">
              <TriangleAlert size={15} aria-hidden /> It cannot be recovered
            </p>
            <p className="mt-1 leading-relaxed">
              Nobody can reset this password, not even an admin. If you both forget it, every message and photo in the chat is lost.
              Tell {partnerName} the new password in person, not in a message.
            </p>
          </div>
          <label className="flex min-h-11 items-center gap-3 text-sm text-slate-200">
            <input
              type="checkbox"
              checked={understood}
              onChange={(event) => setUnderstood(event.target.checked)}
              className="h-5 w-5 shrink-0 accent-rose-500"
              data-testid="private-change-understood"
            />
            I understand a forgotten password cannot be recovered
          </label>
          {error && (
            <p className="text-sm text-rose-200" role="alert" data-testid="private-change-error">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <button type="button" onClick={() => reset('menu')} className="min-h-12 flex-1 rounded-xl border border-white/10 text-sm font-semibold text-slate-200">
              Back
            </button>
            <button
              type="submit"
              disabled={busy || !current || Boolean(problem) || !understood}
              data-testid="private-change-submit"
              className="flex min-h-12 flex-1 items-center justify-center gap-2 rounded-xl bg-rose-600 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}
              {busy ? 'Changing…' : 'Change password'}
            </button>
          </div>
          <p className="text-center text-xs text-slate-500">At least {MIN_PASSWORD_LENGTH} characters.</p>
        </form>
      </Sheet>
    );
  }

  if (mode === 'phone') {
    return (
      <Sheet title="Unlock with this phone" onClose={onClose} testId="private-phone-lock">
        <form onSubmit={(event) => void turnOnPhone(event)} className="space-y-3">
          <p className="text-sm leading-relaxed text-slate-300">
            Enter the chat password once more. After that, your fingerprint, face or screen lock opens the chat on this phone. The
            password still works, and changing it does not turn this off.
          </p>
          <PasswordField label="Chat password" value={current} onChange={setCurrent} testId="private-phone-password" autoComplete="current-password" autoFocus />
          {error && (
            <p className="text-sm text-rose-200" role="alert" data-testid="private-phone-error">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <button type="button" onClick={() => reset('menu')} className="min-h-12 flex-1 rounded-xl border border-white/10 text-sm font-semibold text-slate-200">
              Back
            </button>
            <button
              type="submit"
              disabled={busy || !current}
              data-testid="private-phone-submit"
              className="flex min-h-12 flex-1 items-center justify-center gap-2 rounded-xl bg-rose-600 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Fingerprint size={15} />}
              {busy ? 'Waiting for the phone…' : 'Turn on'}
            </button>
          </div>
        </form>
      </Sheet>
    );
  }

  return (
    <Sheet title="Lock and password" onClose={onClose} testId="private-security">
      <div className="space-y-2">
        <fieldset className="rounded-xl border border-white/10 bg-white/[0.04] p-3" data-testid="private-lock-after">
          <legend className="sr-only">Lock the chat</legend>
          <p className="flex items-center gap-2 text-sm font-semibold text-white" aria-hidden>
            <Timer size={16} className="shrink-0 text-rose-200" /> Lock the chat
          </p>
          <div role="radiogroup" aria-label="Lock the chat" className="mt-2 grid grid-cols-1 gap-1">
            {LOCK_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={lockAfter === option.value}
                onClick={() => chooseLock(option.value)}
                data-testid={`private-lock-after-${option.value}`}
                className={`flex min-h-11 items-center gap-3 rounded-lg px-2 text-left text-sm ${
                  lockAfter === option.value ? 'bg-rose-500/15 text-white' : 'text-slate-200 hover:bg-white/5'
                }`}
              >
                <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${lockAfter === option.value ? 'border-rose-300' : 'border-slate-500'}`}>
                  {lockAfter === option.value && <span className="h-2 w-2 rounded-full bg-rose-300" />}
                </span>
                {option.label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-400">
            On this device only. Closing Jarvis always locks it, and screenshots stay blocked while the chat is on screen.
          </p>
        </fieldset>
        <button
          type="button"
          onClick={() => reset('password')}
          data-testid="private-change-password-open"
          className="flex min-h-12 w-full items-center gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-left text-sm font-semibold text-white"
        >
          <KeyRound size={16} className="shrink-0 text-rose-200" aria-hidden />
          <span className="min-w-0 flex-1">Change password</span>
          <ChevronRight size={16} className="shrink-0 text-slate-400" aria-hidden />
        </button>
        {phone.available && (
          <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2">
            <Fingerprint size={16} className="shrink-0 text-rose-200" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-white" id="private-phone-lock-label">
                Unlock with this phone's lock
              </p>
              <p className="text-xs leading-relaxed text-slate-400">
                Fingerprint, face or screen lock, on this phone only. Changing the chat password does not affect it.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={phone.enabled}
              aria-labelledby="private-phone-lock-label"
              disabled={busy}
              onClick={() => (phone.enabled ? void turnOffPhone() : reset('phone'))}
              data-testid="private-phone-lock-toggle"
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center disabled:opacity-50"
            >
              <span className={`relative h-6 w-11 rounded-full transition-colors ${phone.enabled ? 'bg-rose-600' : 'bg-white/15'}`}>
                <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${phone.enabled ? 'left-[22px]' : 'left-0.5'}`} />
              </span>
            </button>
          </div>
        )}
        {error && (
          <p className="text-sm text-rose-200" role="alert">
            {error}
          </p>
        )}
      </div>
    </Sheet>
  );
}

/**
 * Admin > Users & Devices > Security: "Sign everyone out and reset keys".
 *
 * Every API key is revoked and regenerated, so every phone, browser and
 * device holding an old key is refused (401 with code "sign_in_reset") and
 * has to sign in again. Some things cannot be re-keyed from here (a Windows
 * Guard install, a voice satellite, a key pasted into another app, the home
 * hub's companion apps); the report lists each one with how to enrol it
 * again, and the third-party logins Jarvis stores, which only the provider
 * can rotate.
 *
 * The admin's own key is revoked too. Any background request may then send
 * them to /login before they have read the report, so the panel always shows
 * the last reset's report (GET .../last) once they are back.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, LogIn, ShieldAlert, UserX } from 'lucide-react';
import toast from 'react-hot-toast';
import Modal from '../ui/Modal';
import { confirmDialog } from '../ui/confirmDialog';
import { api } from '../../services/api';
import type { ReEnrolItem, SignInResetPreview, SignInResetResult, ThirdPartyCredential } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { useAuth } from '../../context/AuthContext';

const CONFIRM_WORD = 'RESET';

const KIND_LABELS: Record<ReEnrolItem['kind'], string> = {
  windows_guard: 'Windows Guard',
  esphome_satellite: 'Voice satellites (ESPHome)',
  api_key: 'API keys used by other apps',
  homehub_companion_app: 'HomeHub companion app',
  homehub_long_lived_token: 'HomeHub long-lived tokens',
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind as ReEnrolItem['kind']] ?? kind;
}

/** Re-enrolment items grouped by kind, in the order the server listed them. */
function groupReEnrol(items: ReEnrolItem[]): Array<{ kind: string; items: ReEnrolItem[] }> {
  const groups = new Map<string, ReEnrolItem[]>();
  for (const item of items) {
    const list = groups.get(item.kind) ?? [];
    list.push(item);
    groups.set(item.kind, list);
  }
  return Array.from(groups, ([kind, list]) => ({ kind, items: list }));
}

function ReEnrolList({ items }: { items: ReEnrolItem[] }) {
  const groups = useMemo(() => groupReEnrol(items), [items]);
  if (!items.length) {
    return <p className="text-sm text-slate-400">Nothing needs to be enrolled again by hand.</p>;
  }
  return (
    <div className="space-y-3" data-testid="reenrol-list">
      {groups.map((group) => (
        <div key={group.kind} className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3" data-testid={`reenrol-${group.kind}`}>
          <p className="text-sm font-semibold text-amber-200">{kindLabel(group.kind)}</p>
          <ul className="mt-2 space-y-2">
            {group.items.map((item, i) => (
              <li key={`${item.label}-${item.owner ?? ''}-${i}`} className="text-xs text-slate-300 break-words">
                <span className="font-semibold text-white">{item.label}</span>
                {item.owner ? <span className="text-slate-400"> (@{item.owner})</span> : null}
                {typeof item.count === 'number' ? <span className="text-slate-400"> x{item.count}</span> : null}
                <p className="mt-0.5 text-slate-400">{item.how}</p>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function ThirdPartyList({ items }: { items: ThirdPartyCredential[] }) {
  if (!items.length) return <p className="text-sm text-slate-400">No third-party logins are stored.</p>;
  return (
    <ul className="space-y-1" data-testid="third-party-list">
      {items.map((item) => (
        <li key={item.field} className="flex items-start justify-between gap-3 text-xs text-slate-300">
          <span className="min-w-0 break-words">{item.label}</span>
          <span className="shrink-0 text-slate-400">{item.accounts} {item.accounts === 1 ? 'account' : 'accounts'}</span>
        </li>
      ))}
    </ul>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/5 p-3 min-w-0">
      <p className="text-lg font-bold text-white">{value}</p>
      <p className="text-[11px] text-slate-400 break-words">{label}</p>
    </div>
  );
}

const HOMEHUB_TONE: Record<string, string> = {
  revoked: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-200',
  skipped: 'border-amber-500/20 bg-amber-500/10 text-amber-200',
  failed: 'border-red-500/20 bg-red-500/10 text-red-200',
};

/** What a sign-in reset did: counts, the home hub, what to enrol again, what to rotate at the provider. */
export function SignInResetReport({ result }: { result: SignInResetResult }) {
  const when = new Date(result.at);
  const whenText = Number.isNaN(when.getTime()) ? result.at : when.toLocaleString();
  return (
    <div className="space-y-4" data-testid="sign-in-reset-report">
      <p className="text-sm text-slate-300">
        {result.scope === 'user' && result.username
          ? <>Sign-in reset for @{result.username}</>
          : <>Everyone was signed out</>}
        {' '}on {whenText} by @{result.by}.
      </p>
      <div className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-3">
        <Count label="People signed out" value={result.users_reset} />
        <Count label="API keys revoked" value={result.api_keys_revoked} />
        <Count label="Device keys revoked" value={result.device_agent_keys_revoked} />
      </div>
      <div
        className={`rounded-xl border p-3 text-sm break-words ${HOMEHUB_TONE[result.homehub?.status] ?? HOMEHUB_TONE.skipped}`}
        data-testid="homehub-status"
        role={result.homehub?.status === 'failed' ? 'alert' : undefined}
      >
        <span className="font-semibold">Home hub: </span>
        {result.homehub?.message || `status ${result.homehub?.status ?? 'unknown'}`}
      </div>
      <div>
        <h4 className="mb-2 text-sm font-bold text-white">Needs enrolling again by hand</h4>
        <ReEnrolList items={result.re_enrol ?? []} />
      </div>
      <div>
        <h4 className="text-sm font-bold text-white">Third-party logins</h4>
        <p className="mb-2 text-xs text-slate-400">Jarvis cannot change these: rotate these at the provider.</p>
        <ThirdPartyList items={result.third_party ?? []} />
      </div>
    </div>
  );
}

function PreviewBody({ preview }: { preview: SignInResetPreview }) {
  return (
    <div className="space-y-4" data-testid="sign-in-reset-preview">
      <p className="text-sm text-slate-300">
        Every API key is revoked and a new one made. Every phone, browser and device signed in now
        will have to sign in again, you included.
      </p>
      <div className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-3">
        <Count label="People" value={preview.users} />
        <Count label="API keys" value={preview.api_keys} />
        <Count label="Device keys" value={preview.device_agent_keys} />
      </div>
      <div>
        <h4 className="mb-2 text-sm font-bold text-white">These will need enrolling again by hand</h4>
        <ReEnrolList items={preview.re_enrol ?? []} />
      </div>
      <div>
        <h4 className="text-sm font-bold text-white">Third-party logins (not changed)</h4>
        <p className="mb-2 text-xs text-slate-400">Rotate these at the provider if you think they leaked.</p>
        <ThirdPartyList items={preview.third_party ?? []} />
      </div>
    </div>
  );
}

function SignInAgainButton() {
  const { logout } = useAuth();
  return (
    <button
      type="button"
      onClick={() => void logout('sign_in_reset')}
      className="glass-button min-h-11 w-full sm:w-auto px-4 bg-purple-600 hover:bg-purple-500 text-white font-semibold"
      data-testid="sign-in-again"
    >
      <LogIn size={16} /> Sign in again
    </button>
  );
}

const LAST_RESET_KEY = ['sign-in-reset', 'last'] as const;

/**
 * Dialogs go in the body: these sit inside frosted-glass panels, whose
 * backdrop-filter would otherwise be the fixed layer's frame, so the dialog
 * grew with the page instead of fitting the screen.
 */
function inBody(node: ReactNode) {
  return createPortal(node, document.body);
}

function ResetEveryoneDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState('');
  const preview = useQuery<SignInResetPreview>({
    queryKey: ['sign-in-reset', 'preview'],
    queryFn: () => api.getSignInResetPreview(),
    staleTime: 0,
  });
  const reset = useMutation({
    mutationFn: () => api.resetSignIn(),
    onSuccess: (result) => {
      queryClient.setQueryData(LAST_RESET_KEY, result);
    },
  });
  const result = reset.data;
  const armed = typed.trim() === CONFIRM_WORD;

  return inBody(
    <Modal isOpen onClose={onClose} title={result ? 'Sign-in reset done' : 'Sign everyone out and reset keys?'} size="2xl">
      <div className="space-y-5" data-testid="sign-in-reset-dialog-body">
        {result ? (
          <>
            <SignInResetReport result={result} />
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              {result.caller_signed_out ? (
                <>
                  <p className="text-xs text-slate-400 sm:mr-auto sm:self-center">Your own key was reset too.</p>
                  <SignInAgainButton />
                </>
              ) : (
                <button type="button" onClick={onClose} className="glass-button min-h-11 px-4">Close</button>
              )}
            </div>
          </>
        ) : (
          <>
            {preview.isLoading && <p className="text-sm text-slate-400" role="status">Reading what the reset will touch...</p>}
            {preview.isError && (
              <p className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-200" role="alert">
                {errorText(preview.error, 'Could not read what the reset would touch.')}
              </p>
            )}
            {preview.data && <PreviewBody preview={preview.data} />}
            <div>
              <label htmlFor="sign-in-reset-confirm" className="mb-2 block text-sm font-medium text-slate-300">
                Type {CONFIRM_WORD} to confirm
              </label>
              <input
                id="sign-in-reset-confirm"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                autoCapitalize="characters"
                autoCorrect="off"
                spellCheck={false}
                className="glass-input w-full min-h-11"
                placeholder={CONFIRM_WORD}
              />
            </div>
            {reset.isError && (
              <p className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-200" role="alert">
                {errorText(reset.error, 'The reset failed. Nothing was changed.')}
              </p>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button type="button" onClick={onClose} className="glass-button min-h-11 px-4">Cancel</button>
              <button
                type="button"
                onClick={() => reset.mutate()}
                disabled={!armed || reset.isPending || !preview.data}
                className="glass-button min-h-11 px-4 bg-red-600 hover:bg-red-500 text-white font-semibold disabled:opacity-50"
                data-testid="sign-in-reset-confirm-button"
              >
                {reset.isPending ? 'Resetting...' : 'Sign everyone out'}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>,
  );
}

/** The Security section of the Admin page. */
export default function SignInResetPanel() {
  const [open, setOpen] = useState(false);
  const last = useQuery<SignInResetResult | null>({
    queryKey: LAST_RESET_KEY,
    queryFn: () => api.getLastSignInReset(),
  });

  return (
    <section className="glass-panel p-4 sm:p-6 min-w-0 overflow-hidden" data-testid="admin-security">
      <div className="mb-4">
        <h3 className="flex items-center gap-3 text-xl font-bold text-white">
          <ShieldAlert size={20} className="text-red-400" />
          Security
        </h3>
        <p className="mt-1 text-sm text-slate-400">
          If a key or a phone may have been lost or leaked, sign everyone out. Every key is replaced, and
          the report says what has to be enrolled again by hand.
        </p>
      </div>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="glass-button min-h-11 w-full sm:w-auto px-4 bg-red-600/80 hover:bg-red-500 text-white font-semibold"
      >
        <KeyRound size={16} /> Sign everyone out and reset keys
      </button>

      {last.isError && (
        <p className="mt-4 text-sm text-amber-200" role="alert">
          {errorText(last.error, 'Could not read the last sign-in reset.')}
        </p>
      )}
      {last.data && (
        <div className="mt-6 border-t border-white/5 pt-4" data-testid="last-sign-in-reset">
          <h4 className="mb-3 text-sm font-black uppercase tracking-widest text-slate-400">Last reset</h4>
          <SignInResetReport result={last.data} />
        </div>
      )}

      {open && <ResetEveryoneDialog onClose={() => setOpen(false)} />}
    </section>
  );
}

/** A per-person "Reset sign-in" action for the admin user lists. */
export function UserSignInResetButton({ username, name }: { username: string; name?: string }) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<SignInResetResult | null>(null);
  const reset = useMutation({
    mutationFn: () => api.resetSignIn(username),
    onSuccess: (data) => {
      queryClient.setQueryData(LAST_RESET_KEY, data);
      setResult(data);
    },
    onError: (err) => toast.error(errorText(err, `Could not reset ${name || username}'s sign-in`)),
  });

  const ask = async () => {
    if (await confirmDialog({
      title: `Reset ${name || username}'s sign-in?`,
      body: `Every key @${username} holds is revoked and replaced. Their phones, browsers and devices have to sign in again.`,
      confirmLabel: 'Reset sign-in',
      danger: true,
    })) {
      reset.mutate();
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => void ask()}
        disabled={reset.isPending}
        className="rounded-xl p-2 min-h-11 min-w-11 pointer-coarse:min-h-11 flex items-center justify-center text-slate-400 transition hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
        aria-label={`Reset sign-in for ${username}`}
        title="Sign this person out everywhere and replace their keys"
        data-testid={`reset-sign-in-${username}`}
      >
        <UserX size={16} />
      </button>
      {result && inBody(
        <Modal isOpen onClose={() => setResult(null)} title="Sign-in reset done" size="2xl">
          <div className="space-y-5">
            <SignInResetReport result={result} />
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              {result.caller_signed_out ? <SignInAgainButton /> : (
                <button type="button" onClick={() => setResult(null)} className="glass-button min-h-11 px-4">Close</button>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

/** The badge the admin user lists show on a test account. */
export function TestAccountBadge() {
  return (
    <span
      className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-black uppercase tracking-widest text-sky-300"
      title="A test account: left out of the household's lists"
      data-testid="test-account-badge"
    >
      Test account
    </span>
  );
}

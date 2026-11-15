import { useState, type FormEvent } from 'react';
import { KeyRound, Loader2, Lock, TriangleAlert } from 'lucide-react';
import { newChatSecrets } from '../../lib/privateChatCrypto';
import { api } from '../../services/api';
import Sheet from './Sheet';
import { errorText, MIN_PASSWORD_LENGTH, passwordProblem, type PrivateChatInfo, type PrivateChatStatus } from './privateChatModel';

/**
 * Start the parents' private chat: pick the other parent, choose a password
 * together (in person), confirm it, and accept that it cannot be recovered.
 * The chat's key is made here at random and wrapped under the password; only
 * the wrapped key, its salt and round count, the verifier and a password proof
 * (kept by the server as a hash) go to the server.
 */
export default function NewPrivateChatSheet({
  partners,
  onCreated,
  onClose,
}: {
  partners: PrivateChatStatus['partners'];
  onCreated: (chat: PrivateChatInfo, key: CryptoKey) => void;
  onClose: () => void;
}) {
  const [partner, setPartner] = useState(partners.length === 1 ? partners[0].username : '');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const problem = passwordProblem(password, confirm);
  const partnerName = partners.find((p) => p.username === partner)?.display_name || 'the other parent';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (problem || !partner || !understood || busy) return;
    setBusy(true);
    setError('');
    try {
      const secrets = await newChatSecrets(password);
      const chat = await api.createPrivateChat({
        partner,
        version: 2,
        kdf: secrets.kdf,
        wrapped_key: secrets.wrapped_key,
        verifier: secrets.verifier,
        auth: secrets.auth,
      });
      setPassword('');
      setConfirm('');
      onCreated(chat, secrets.key);
    } catch (e) {
      setError(errorText(e, 'Could not start the private chat'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet title="Private chat" onClose={onClose} testId="new-private-chat">
      <form onSubmit={(event) => void submit(event)} className="space-y-3">
        <p className="flex items-start gap-2 text-sm leading-relaxed text-slate-300">
          <Lock size={16} className="mt-0.5 shrink-0 text-rose-200" aria-hidden />
          <span>A chat only the two of you can read. Messages and photos are encrypted on your phones with a password you choose together.</span>
        </p>

        {partners.length > 1 && (
          <fieldset>
            <legend className="mb-1 text-xs font-semibold text-slate-300">With</legend>
            <div className="flex flex-wrap gap-2">
              {partners.map((p) => (
                <button
                  key={p.username}
                  type="button"
                  aria-pressed={partner === p.username}
                  onClick={() => setPartner(p.username)}
                  className={`min-h-11 rounded-xl border px-3 text-sm ${
                    partner === p.username ? 'border-rose-300/60 bg-rose-500/20 text-white' : 'border-white/10 text-slate-200'
                  }`}
                >
                  {p.display_name}
                </button>
              ))}
            </div>
          </fieldset>
        )}
        {partners.length === 1 && (
          <p className="text-sm text-slate-200">
            With <span className="font-semibold text-white">{partnerName}</span>
          </p>
        )}

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-slate-300">Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            autoCapitalize="off"
            spellCheck={false}
            data-testid="private-new-password"
            className="glass-input min-h-11 w-full text-base sm:text-sm"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-slate-300">Type it again</span>
          <input
            type="password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            autoComplete="new-password"
            autoCapitalize="off"
            spellCheck={false}
            data-testid="private-confirm-password"
            className="glass-input min-h-11 w-full text-base sm:text-sm"
          />
        </label>
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
            Nobody can reset this password, not even an admin: the server never sees it. If you both forget it, every message
            and photo in the chat is lost. Tell {partnerName} the password in person, not in a message.
          </p>
        </div>
        <label className="flex min-h-11 items-center gap-3 text-sm text-slate-200">
          <input
            type="checkbox"
            checked={understood}
            onChange={(event) => setUnderstood(event.target.checked)}
            className="h-5 w-5 shrink-0 accent-rose-500"
            data-testid="private-understood"
          />
          I understand a forgotten password cannot be recovered
        </label>

        {error && (
          <p className="text-sm text-rose-200" role="alert">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || Boolean(problem) || !partner || !understood}
          data-testid="private-create"
          className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-rose-600 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}
          {busy ? 'Encrypting…' : 'Start private chat'}
        </button>
        <p className="text-center text-xs text-slate-500">At least {MIN_PASSWORD_LENGTH} characters. A short sentence is easy to share and hard to guess.</p>
      </form>
    </Sheet>
  );
}

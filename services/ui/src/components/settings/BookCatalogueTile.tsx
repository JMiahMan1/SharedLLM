import { useState } from 'react';
import { BookOpen, CheckCircle, FlaskConical, Library, LockKeyhole, Save, XCircle } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { UserProfile } from '../../services/api';
import type { BookCatalogueId, BookCatalogueSource, BookCatalogueTest, BookCatalogues } from '../../types/api';
import { errorText } from '../../lib/errorText';
import Modal from '../ui/Modal';
import PasswordField from '../ui/PasswordField';
import HelpTooltip from '../ui/HelpTooltip';
import { Toggle } from '../ui/Toggle';

export const CATALOGUE_NAMES: Record<BookCatalogueId, string> = {
  google_books: 'Google Books',
  open_library: 'Open Library',
};

const KEY_FIELD = 'google_books_key';

/** Before identity reports it (an older identity): what the defaults would be. */
const FALLBACK: BookCatalogues = {
  order: ['google_books', 'open_library'],
  sources: {
    google_books: { name: 'Google Books', enabled: false, enabled_from: 'default', ready: false, key_set: false,
      key_source: 'absent', reason: 'Google Books: not set up (add an API key in Settings > Accounts & Integrations)' },
    open_library: { name: 'Open Library', enabled: true, enabled_from: 'default', ready: true, reason: '', contact_email: '' },
  },
};

/** The one line a catalogue card shows, and whether it is a problem. */
export function catalogueLine(id: BookCatalogueId, source: BookCatalogueSource | undefined): { text: string; problem: boolean } {
  if (!source) return { text: 'Not set up', problem: false };
  if (source.ready) {
    if (id === 'google_books') {
      return { text: source.key_source === 'household' ? 'On, with the household key' : 'On, with your own key', problem: false };
    }
    return { text: 'On', problem: false };
  }
  if (source.enabled) return { text: 'On, but no API key is set', problem: true };
  if (id === 'google_books' && !source.key_set) return { text: 'Not set up', problem: false };
  return { text: 'Off', problem: false };
}

/**
 * Google Books or Open Library in Accounts & Integrations (docs/INTEGRATIONS.md).
 *
 * The library fills in a book's details from these catalogues, only the ones
 * turned on here, in the order set here. Like the abuse.ch key, the default
 * user's settings are the household's: everyone who has not set their own
 * follows them. Google Books needs an API key (never asked keyless); Open
 * Library needs none but asks for a contact email. Test does one lookup and
 * shows exactly what the catalogue answered.
 */
export default function BookCatalogueTile({ id, userData }: { id: BookCatalogueId; userData?: UserProfile | null }) {
  const name = CATALOGUE_NAMES[id];
  const other: BookCatalogueId = id === 'google_books' ? 'open_library' : 'google_books';
  const [isOpen, setIsOpen] = useState(false);
  const [key, setKey] = useState('');
  const [email, setEmail] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [test, setTest] = useState<BookCatalogueTest | null>(null);
  const queryClient = useQueryClient();
  const isDefault = Boolean(userData?.is_system_default);
  const catalogues = userData?.book_metadata_status ?? FALLBACK;
  const source = catalogues.sources[id];
  const line = catalogueLine(id, source);
  const keySaved = (userData?.saved_credentials ?? []).includes(KEY_FIELD);
  const first = catalogues.order[0] === id;
  const slug = id.replace('_', '-');

  const save = useMutation({
    mutationFn: (body: Partial<UserProfile>) => api.updateProfile(body),
    onSuccess: (_data, body) => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      queryClient.invalidateQueries({ queryKey: ['library', 'catalogues'] });
      setKey('');
      setConfirmRemove(false);
      const what = 'clear_fields' in body ? `Your ${name} key was removed`
        : KEY_FIELD in body ? `${name} key saved; ${name} is on`
        : `${name} settings saved`;
      toast.success(what);
    },
    onError: (error: unknown) => toast.error(errorText(error, `The ${name} settings were not saved`)),
  });

  const runTest = useMutation({
    mutationFn: () => api.testBookCatalogue({
      source: id,
      ...(id === 'google_books' && key.trim() ? { api_key: key.trim() } : {}),
      ...(id === 'open_library' && email.trim() ? { contact_email: email.trim() } : {}),
    }),
    onSuccess: (result) => setTest(result),
    onError: (error: unknown) => setTest({ ok: false, source: id, status: null, message: errorText(error, `${name} could not be tested`) }),
  });

  const open = () => {
    setKey('');
    setEmail(source?.contact_email_from === 'own' ? source.contact_email ?? '' : '');
    setConfirmRemove(false);
    setTest(null);
    setIsOpen(true);
  };

  const enabledKey = id === 'google_books' ? 'google_books_enabled' : 'open_library_enabled';
  const setEnabled = (next: boolean) => save.mutate({ book_metadata: { [enabledKey]: next } } as Partial<UserProfile>);
  const followHousehold = () => save.mutate({ book_metadata: { [enabledKey]: null } } as Partial<UserProfile>);
  const setFirst = (which: BookCatalogueId) =>
    save.mutate({ book_metadata: { order: [which, which === id ? other : id] } } as Partial<UserProfile>);
  const Icon = id === 'google_books' ? BookOpen : Library;

  const explain = (() => {
    if (id === 'google_books') {
      if (source?.key_source === 'household') return 'Lookups use the household key (set on the default user). Add your own only if your lookups should use it instead.';
      if (source?.key_source === 'own') return isDefault ? 'This is the household key: everyone who has not set their own uses it.' : 'Your lookups use your own key. Remove it to use the household key, if one is set.';
      return isDefault ? 'No household key yet. Add one here: everyone who has not set their own will use it.' : 'No key is set yet. Add your own here, or ask an admin to add one for the household.';
    }
    return 'Open Library needs no key. It asks apps to say who to contact about their requests; the email below goes in each request for that.';
  })();

  return (
    <>
      <div className="glass-panel p-4 md:p-6 flex flex-row md:flex-col items-center text-left md:text-center gap-3 md:gap-4 group hover:border-purple-500/30 transition-all duration-500" data-testid={`${slug}-tile`}>
        <div className="p-3 md:p-4 rounded-2xl shrink-0 bg-sky-500/20 text-sky-400 group-hover:scale-110 transition-transform duration-500">
          <Icon className="w-6 h-6 md:w-8 md:h-8" />
        </div>
        <div className="flex-1 md:flex-none min-w-0">
          <h3 className="font-bold text-white break-words">{name}</h3>
          <p className={`text-[11px] mt-1 break-words ${line.problem ? 'text-rose-300' : 'text-slate-400'}`} data-testid={`integration-status-${slug}`}>{line.text}</p>
        </div>
        <button onClick={open} aria-label={`Manage ${name}`} className="glass-button shrink-0 md:w-full text-[10px] uppercase font-bold tracking-widest md:mt-2 min-h-11 px-3">
          {source?.ready ? 'Manage' : 'Set up'}
        </button>
        <div className="hidden md:flex items-center gap-2 mt-2">
          <div className={`w-1.5 h-1.5 rounded-full ${source?.ready ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : line.problem ? 'bg-rose-500' : 'bg-slate-600'}`} />
          <span className="text-[9px] uppercase font-black tracking-tighter text-slate-500">{source?.ready ? 'Filling in books' : 'Not used'}</span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={name}>
        <div className="space-y-5" data-testid={`${slug}-dialog`}>
          <p className="text-xs text-slate-300 leading-relaxed">
            The family library fills in a book&apos;s title, authors, description, ISBN and cover from the book catalogues
            turned on here, in the order set here. Only the book&apos;s title, author or ISBN is sent.
          </p>

          <div className={`p-4 rounded-xl flex gap-3 border ${line.problem ? 'bg-rose-500/10 border-rose-500/20' : 'bg-emerald-500/10 border-emerald-500/20'}`} data-testid={`${slug}-status`}>
            {line.problem
              ? <XCircle className="text-rose-400 shrink-0" size={20} aria-hidden />
              : <CheckCircle className={source?.ready ? 'text-emerald-400 shrink-0' : 'text-slate-500 shrink-0'} size={20} aria-hidden />}
            <div className="text-xs text-slate-300 leading-relaxed min-w-0">
              <p className="font-bold text-white mb-1">{line.text}</p>
              {source && !source.ready && source.reason && <p className="mb-1">{source.reason}.</p>}
              <p>{explain}</p>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-white">Use {name} for book details</p>
              <p className="text-[11px] text-slate-400">
                {source?.enabled_from === 'own' ? (isDefault ? 'Set for the household' : 'Your own choice')
                  : source?.enabled_from === 'household' ? 'Following the household setting' : 'The default'}
                {source?.enabled_from === 'own' && !isDefault && (
                  <button type="button" className="ml-2 underline pointer-coarse:min-h-11" onClick={followHousehold}>Follow the household</button>
                )}
              </p>
            </div>
            <Toggle checked={Boolean(source?.enabled)} onChange={setEnabled} disabled={save.isPending}
              ariaLabel={`Use ${name} for book details`} testId={`${slug}-enabled`} />
          </div>

          {id === 'google_books' ? (
            <div>
              <div className="flex items-center mb-2">
                <label htmlFor="google-books-key" className="text-[10px] text-slate-400 uppercase font-black tracking-widest">
                  {isDefault ? 'Household Google Books API key' : keySaved ? 'Your Google Books API key' : 'Your own Google Books API key (optional)'}
                </label>
                <HelpTooltip docName="INTEGRATIONS.md" sectionTitle="Google Books" label="Google Books API key" />
              </div>
              <PasswordField
                id="google-books-key"
                aria-label="Google Books API key"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                autoComplete="off"
                placeholder={keySaved ? 'Saved (paste a new key to replace it)' : 'Paste the API key from Google Cloud Console'}
                className="glass-input w-full text-sm py-3 bg-black/20 focus:bg-black/40"
              />
              <p className="mt-1 text-[11px] text-slate-500">
                Free: in Google Cloud Console turn on the Books API, then Credentials, Create credentials, API key.
                Saving a key turns Google Books on.
              </p>
            </div>
          ) : (
            <div>
              <div className="flex items-center mb-2">
                <label htmlFor="open-library-email" className="text-[10px] text-slate-400 uppercase font-black tracking-widest">
                  Contact email (optional)
                </label>
                <HelpTooltip docName="INTEGRATIONS.md" sectionTitle="Open Library" label="Contact email" />
              </div>
              <input
                id="open-library-email"
                type="email"
                inputMode="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={source?.contact_email && source.contact_email_from !== 'own' ? `${source.contact_email} (household)` : 'you@example.com'}
                className="glass-input w-full text-sm py-3 bg-black/20 focus:bg-black/40"
              />
            </div>
          )}

          <div className="flex flex-col sm:flex-row gap-2">
            {id === 'google_books' ? (
              <button
                type="button"
                onClick={() => save.mutate({ [KEY_FIELD]: key.trim(), book_metadata: { google_books_enabled: true } } as Partial<UserProfile>)}
                disabled={save.isPending || key.trim() === ''}
                className="glass-button flex-1 min-h-11 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
              >
                <Save size={18} /> Save key
              </button>
            ) : (
              <button
                type="button"
                onClick={() => save.mutate({ book_metadata: { open_library_email: email.trim() } } as Partial<UserProfile>)}
                disabled={save.isPending}
                className="glass-button flex-1 min-h-11 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
              >
                <Save size={18} /> Save email
              </button>
            )}
            <button
              type="button"
              onClick={() => runTest.mutate()}
              disabled={runTest.isPending}
              className="glass-button flex-1 min-h-11 py-3 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
            >
              <FlaskConical size={18} /> {runTest.isPending ? `Asking ${name}...` : 'Test'}
            </button>
          </div>

          {test && (
            <div role="status" data-testid={`${slug}-test-result`}
              className={`p-3 rounded-xl border text-xs leading-relaxed break-words ${test.ok ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-100' : 'bg-rose-500/10 border-rose-500/20 text-rose-100'}`}>
              {test.message}
            </div>
          )}

          <label className="flex items-center justify-between gap-3 text-sm text-white">
            <span>Ask first</span>
            <select
              value={catalogues.order[0]}
              onChange={(e) => setFirst(e.target.value as BookCatalogueId)}
              disabled={save.isPending}
              aria-label="Which catalogue to ask first"
              className="glass-input min-h-11 text-sm"
              data-testid={`${slug}-order`}
            >
              <option value="google_books">Google Books</option>
              <option value="open_library">Open Library</option>
            </select>
          </label>
          <p className="-mt-3 text-[11px] text-slate-500">
            {first ? `${name} is asked first; ${CATALOGUE_NAMES[other]} fills in what it lacks.` : `${CATALOGUE_NAMES[other]} is asked first; ${name} fills in what it lacks.`}
          </p>

          {id === 'google_books' && (
            <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl flex gap-3">
              <LockKeyhole className="text-indigo-400 shrink-0" size={18} aria-hidden />
              <p className="text-[11px] text-slate-300 leading-relaxed">
                Stored encrypted and never shown again: a saved key shows as Saved. Google Books is never asked without a key.
              </p>
            </div>
          )}

          {id === 'google_books' && keySaved && (
            <div className="border-t border-white/5 pt-3">
              {confirmRemove ? (
                <div className="flex flex-wrap items-center gap-3 text-xs text-rose-200" role="alert">
                  {isDefault ? 'Remove the household key? Nobody without their own key can use Google Books.' : 'Remove your key?'}
                  <button type="button" className="font-semibold underline min-h-11 px-2"
                    onClick={() => save.mutate({ clear_fields: [KEY_FIELD] } as Partial<UserProfile>)}>
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

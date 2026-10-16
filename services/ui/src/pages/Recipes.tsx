import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Loader2, Plus, Save, Trash2, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { saveNote } from '../lib/noteSave';
import { noteBody } from '../lib/notesMeta';
import type { ExecutionResponse } from '../types/api';
import SendAsSelector from '../components/chat/SendAsSelector';
import { useSendAsPref } from '../components/chat/sendAsPref';

export const RECIPES_DIR = 'Recipes';
const STORAGE = 'nextcloud';

interface RecipeSummary {
  title: string;
  path: string;
  modified?: string;
}

function notesOf(response: ExecutionResponse | undefined): RecipeSummary[] {
  const detail = response?.detail as { notes?: RecipeSummary[] } | undefined;
  return detail?.notes ?? [];
}

/**
 * Family recipes, stored as markdown in Nextcloud under `Recipes/`.
 *
 * The same notes storage the rest of the house uses means one backup, one sync
 * and one mobile app. Because notes resolve to the caller's own account, an
 * admin can switch to the Admin identity with the shared "Send as" control to
 * keep one house cookbook — and every other member keeps their own.
 */
export default function Recipes() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<RecipeSummary | null>(null);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [saving, setSaving] = useState(false);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const [sendAs, setSendAs] = useSendAsPref(isAdmin, 'notes');
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  const { data, isLoading } = useQuery({
    queryKey: ['recipes', asUser ?? 'me'],
    queryFn: () => api.listNotes({ storage: STORAGE, directories: [RECIPES_DIR], as_user: asUser }),
  });

  const recipes = notesOf(data);

  const read = useMutation({
    mutationFn: (recipe: RecipeSummary) => api.readNote(recipe.title, STORAGE, recipe.path, asUser),
    onSuccess: (res: ExecutionResponse) => {
      // note_read returns the whole file; the editor shows the recipe without
      // the stored header, or every save would stack another copy of it.
      if (res.status === 'SUCCESS') setBody(noteBody(res.message ?? ''));
      else toast.error(res.message || 'Could not open that recipe');
    },
    onError: (error: Error) => toast.error(error.message || 'Could not open that recipe'),
  });

  const startNew = () => {
    setSelected(null);
    setTitle('');
    setBody('');
  };

  const save = async () => {
    const name = title.trim();
    if (!name) {
      toast.error('Give the recipe a name');
      return;
    }
    setSaving(true);
    try {
      // Editing an open recipe saves over it (or renames it); otherwise it is new.
      const saved = await saveNote({
        title: name,
        content: body,
        category: RECIPES_DIR,
        previous: selected ? { title: selected.title, category: RECIPES_DIR, path: selected.path } : undefined,
        asUser,
      });
      if (saved.response.status === 'SUCCESS') {
        toast.success('Recipe saved');
        if (saved.leftOldCopy) toast.error('Saved under the new name, but the old copy is still there');
        startNew();
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(saved.response.message || 'Could not save the recipe');
      }
    } catch {
      toast.error('Could not save the recipe');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (recipe: RecipeSummary) => {
    if (!window.confirm(`Delete the recipe "${recipe.title}"?`)) return;
    try {
      const res = await api.deleteNote(recipe.title, STORAGE, recipe.path, asUser);
      if (res.status === 'SUCCESS') {
        toast.success('Recipe removed');
        if (selected?.path === recipe.path) startNew();
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(res.message || 'Could not remove that recipe');
      }
    } catch {
      toast.error('Could not remove that recipe');
    }
  };

  return (
    <div className="space-y-4" data-testid="recipes">
      <div className="glass-panel p-4 rounded-2xl border border-white/5 space-y-2">
        <div className="flex items-center gap-2">
          <BookOpen size={16} className="text-orange-300" />
          <span className="text-sm font-semibold text-slate-200">Family recipes</span>
          {asUser === 'admin' && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-200">
              <Users size={11} /> shared cookbook
            </span>
          )}
        </div>
        <p className="text-xs text-slate-400">
          Saved in Nextcloud under <code className="text-slate-300">{RECIPES_DIR}/</code> — yours, or the house account
          when an admin switches.
        </p>
        {isAdmin && <SendAsSelector value={sendAs} onChange={(next) => { startNew(); setSendAs(next); }} />}
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-2">
          {isLoading && <p className="text-xs text-slate-500">Loading recipes…</p>}
          {!isLoading && recipes.length === 0 && (
            <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
              No recipes yet — add the first one on the right.
            </p>
          )}
          {recipes.map((recipe) => (
            <div
              key={recipe.path}
              className={`flex items-center gap-2 rounded-xl border p-1.5 pl-3 ${
                selected?.path === recipe.path ? 'border-purple-400/40 bg-purple-500/10' : 'border-white/5 bg-white/5'
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setSelected(recipe);
                  setTitle(recipe.title);
                  read.mutate(recipe);
                }}
                aria-current={selected?.path === recipe.path ? 'true' : undefined}
                className="min-h-11 min-w-0 flex-1 truncate text-left text-sm text-slate-100"
              >
                {recipe.title}
              </button>
              <button
                type="button"
                aria-label={`Delete ${recipe.title}`}
                onClick={() => void remove(recipe)}
                className="glass-button flex h-11 w-11 shrink-0 items-center justify-center p-0"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              {selected ? 'Editing' : 'New recipe'}
            </span>
            {selected && (
              <button type="button" onClick={startNew} className="glass-button min-h-11 px-3 text-xs">
                <Plus size={14} /> New recipe
              </button>
            )}
          </div>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Recipe name"
            aria-label="Recipe name"
            className="glass-input min-h-11 w-full px-3 py-2 text-base sm:text-sm"
          />
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="Ingredients and steps (markdown)"
            aria-label="Recipe body"
            rows={10}
            className="glass-input w-full resize-y px-3 py-2 text-base sm:text-sm"
          />
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : selected ? <Save size={14} /> : <Plus size={14} />}
            {selected ? 'Save changes' : 'Save recipe'}
          </button>
        </div>
      </div>
    </div>
  );
}

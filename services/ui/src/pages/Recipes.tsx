import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Loader2, Plus, Trash2, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { ExecutionResponse } from '../types/api';
import SendAsSelector, { useSendAsPref } from '../components/chat/SendAsSelector';

export const RECIPES_DIR = 'Recipes';
const STORAGE = 'nextcloud';

interface RecipeSummary {
  title: string;
  path?: string;
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
  const [sendAs, setSendAs] = useSendAsPref(isAdmin);
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  const { data, isLoading } = useQuery({
    queryKey: ['recipes', asUser ?? 'me'],
    queryFn: () => api.listNotes({ storage: STORAGE, directories: [RECIPES_DIR], as_user: asUser }),
  });

  const recipes = notesOf(data);

  const read = useMutation({
    mutationFn: (recipe: RecipeSummary) => api.readNote(recipe.title, STORAGE, recipe.path, asUser),
    onSuccess: (res: ExecutionResponse) => {
      // note_read returns the markdown body as the message text.
      setBody(res.status === 'SUCCESS' ? res.message ?? '' : '');
    },
    onError: (error: Error) => toast.error(error.message || 'Could not open that recipe'),
  });

  const save = async () => {
    const name = title.trim();
    if (!name) {
      toast.error('Give the recipe a name');
      return;
    }
    setSaving(true);
    try {
      const res = await api.writeNote({
        title: name,
        content: body,
        category: RECIPES_DIR,
        storage: STORAGE,
        as_user: asUser,
      });
      if (res.status === 'SUCCESS') {
        toast.success('Recipe saved');
        setTitle('');
        setBody('');
        setSelected(null);
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(res.message || 'Could not save the recipe');
      }
    } finally {
      setSaving(false);
    }
  };

  const remove = async (recipe: RecipeSummary) => {
    const res = await api.deleteNote(recipe.title, STORAGE, recipe.path, asUser);
    if (res.status === 'SUCCESS') {
      toast.success('Recipe removed');
      if (selected?.title === recipe.title) {
        setSelected(null);
        setBody('');
      }
      queryClient.invalidateQueries({ queryKey: ['recipes'] });
    } else {
      toast.error(res.message || 'Could not remove that recipe');
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
        {isAdmin && <SendAsSelector value={sendAs} onChange={setSendAs} />}
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
              key={recipe.path || recipe.title}
              className={`flex items-center gap-2 rounded-xl border p-2.5 ${
                selected?.title === recipe.title ? 'border-purple-400/40 bg-purple-500/10' : 'border-white/5 bg-white/5'
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setSelected(recipe);
                  setTitle(recipe.title);
                  read.mutate(recipe);
                }}
                className="min-h-11 flex-1 text-left text-sm text-slate-100"
              >
                {recipe.title}
              </button>
              <button
                type="button"
                aria-label={`Delete ${recipe.title}`}
                onClick={() => void remove(recipe)}
                className="glass-button p-2"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <div className="space-y-2">
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Recipe name"
            aria-label="Recipe name"
            className="glass-input w-full px-3 py-2 text-sm"
          />
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="Ingredients and steps (markdown)"
            aria-label="Recipe body"
            rows={10}
            className="glass-input w-full resize-y px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Save recipe
          </button>
        </div>
      </div>
    </div>
  );
}
